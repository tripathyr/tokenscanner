var xrpl;
/******/ (() => { // webpackBootstrap
/******/ 	var __webpack_modules__ = ({

/***/ "../../node_modules/@noble/curves/_shortw_utils.js":
/*!*********************************************************!*\
  !*** ../../node_modules/@noble/curves/_shortw_utils.js ***!
  \*********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.getHash = getHash;
exports.createCurve = createCurve;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
const hmac_1 = __webpack_require__(/*! @noble/hashes/hmac */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/hmac.js");
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
const weierstrass_js_1 = __webpack_require__(/*! ./abstract/weierstrass.js */ "../../node_modules/@noble/curves/abstract/weierstrass.js");
// connects noble-curves to noble-hashes
function getHash(hash) {
    return {
        hash,
        hmac: (key, ...msgs) => (0, hmac_1.hmac)(hash, key, (0, utils_1.concatBytes)(...msgs)),
        randomBytes: utils_1.randomBytes,
    };
}
function createCurve(curveDef, defHash) {
    const create = (hash) => (0, weierstrass_js_1.weierstrass)({ ...curveDef, ...getHash(hash) });
    return Object.freeze({ ...create(defHash), create });
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/curve.js":
/*!**********************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/curve.js ***!
  \**********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.wNAF = wNAF;
exports.pippenger = pippenger;
exports.precomputeMSMUnsafe = precomputeMSMUnsafe;
exports.validateBasic = validateBasic;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
// Abelian group utilities
const modular_js_1 = __webpack_require__(/*! ./modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
const _0n = BigInt(0);
const _1n = BigInt(1);
function constTimeNegate(condition, item) {
    const neg = item.negate();
    return condition ? neg : item;
}
function validateW(W, bits) {
    if (!Number.isSafeInteger(W) || W <= 0 || W > bits)
        throw new Error('invalid window size, expected [1..' + bits + '], got W=' + W);
}
function calcWOpts(W, bits) {
    validateW(W, bits);
    const windows = Math.ceil(bits / W) + 1; // +1, because
    const windowSize = 2 ** (W - 1); // -1 because we skip zero
    return { windows, windowSize };
}
function validateMSMPoints(points, c) {
    if (!Array.isArray(points))
        throw new Error('array expected');
    points.forEach((p, i) => {
        if (!(p instanceof c))
            throw new Error('invalid point at index ' + i);
    });
}
function validateMSMScalars(scalars, field) {
    if (!Array.isArray(scalars))
        throw new Error('array of scalars expected');
    scalars.forEach((s, i) => {
        if (!field.isValid(s))
            throw new Error('invalid scalar at index ' + i);
    });
}
// Since points in different groups cannot be equal (different object constructor),
// we can have single place to store precomputes
const pointPrecomputes = new WeakMap();
const pointWindowSizes = new WeakMap(); // This allows use make points immutable (nothing changes inside)
function getW(P) {
    return pointWindowSizes.get(P) || 1;
}
// Elliptic curve multiplication of Point by scalar. Fragile.
// Scalars should always be less than curve order: this should be checked inside of a curve itself.
// Creates precomputation tables for fast multiplication:
// - private scalar is split by fixed size windows of W bits
// - every window point is collected from window's table & added to accumulator
// - since windows are different, same point inside tables won't be accessed more than once per calc
// - each multiplication is 'Math.ceil(CURVE_ORDER / 𝑊) + 1' point additions (fixed for any scalar)
// - +1 window is neccessary for wNAF
// - wNAF reduces table size: 2x less memory + 2x faster generation, but 10% slower multiplication
// TODO: Research returning 2d JS array of windows, instead of a single window. This would allow
// windows to be in different memory locations
function wNAF(c, bits) {
    return {
        constTimeNegate,
        hasPrecomputes(elm) {
            return getW(elm) !== 1;
        },
        // non-const time multiplication ladder
        unsafeLadder(elm, n, p = c.ZERO) {
            let d = elm;
            while (n > _0n) {
                if (n & _1n)
                    p = p.add(d);
                d = d.double();
                n >>= _1n;
            }
            return p;
        },
        /**
         * Creates a wNAF precomputation window. Used for caching.
         * Default window size is set by `utils.precompute()` and is equal to 8.
         * Number of precomputed points depends on the curve size:
         * 2^(𝑊−1) * (Math.ceil(𝑛 / 𝑊) + 1), where:
         * - 𝑊 is the window size
         * - 𝑛 is the bitlength of the curve order.
         * For a 256-bit curve and window size 8, the number of precomputed points is 128 * 33 = 4224.
         * @param elm Point instance
         * @param W window size
         * @returns precomputed point tables flattened to a single array
         */
        precomputeWindow(elm, W) {
            const { windows, windowSize } = calcWOpts(W, bits);
            const points = [];
            let p = elm;
            let base = p;
            for (let window = 0; window < windows; window++) {
                base = p;
                points.push(base);
                // =1, because we skip zero
                for (let i = 1; i < windowSize; i++) {
                    base = base.add(p);
                    points.push(base);
                }
                p = base.double();
            }
            return points;
        },
        /**
         * Implements ec multiplication using precomputed tables and w-ary non-adjacent form.
         * @param W window size
         * @param precomputes precomputed tables
         * @param n scalar (we don't check here, but should be less than curve order)
         * @returns real and fake (for const-time) points
         */
        wNAF(W, precomputes, n) {
            // TODO: maybe check that scalar is less than group order? wNAF behavious is undefined otherwise
            // But need to carefully remove other checks before wNAF. ORDER == bits here
            const { windows, windowSize } = calcWOpts(W, bits);
            let p = c.ZERO;
            let f = c.BASE;
            const mask = BigInt(2 ** W - 1); // Create mask with W ones: 0b1111 for W=4 etc.
            const maxNumber = 2 ** W;
            const shiftBy = BigInt(W);
            for (let window = 0; window < windows; window++) {
                const offset = window * windowSize;
                // Extract W bits.
                let wbits = Number(n & mask);
                // Shift number by W bits.
                n >>= shiftBy;
                // If the bits are bigger than max size, we'll split those.
                // +224 => 256 - 32
                if (wbits > windowSize) {
                    wbits -= maxNumber;
                    n += _1n;
                }
                // This code was first written with assumption that 'f' and 'p' will never be infinity point:
                // since each addition is multiplied by 2 ** W, it cannot cancel each other. However,
                // there is negate now: it is possible that negated element from low value
                // would be the same as high element, which will create carry into next window.
                // It's not obvious how this can fail, but still worth investigating later.
                // Check if we're onto Zero point.
                // Add random point inside current window to f.
                const offset1 = offset;
                const offset2 = offset + Math.abs(wbits) - 1; // -1 because we skip zero
                const cond1 = window % 2 !== 0;
                const cond2 = wbits < 0;
                if (wbits === 0) {
                    // The most important part for const-time getPublicKey
                    f = f.add(constTimeNegate(cond1, precomputes[offset1]));
                }
                else {
                    p = p.add(constTimeNegate(cond2, precomputes[offset2]));
                }
            }
            // JIT-compiler should not eliminate f here, since it will later be used in normalizeZ()
            // Even if the variable is still unused, there are some checks which will
            // throw an exception, so compiler needs to prove they won't happen, which is hard.
            // At this point there is a way to F be infinity-point even if p is not,
            // which makes it less const-time: around 1 bigint multiply.
            return { p, f };
        },
        /**
         * Implements ec unsafe (non const-time) multiplication using precomputed tables and w-ary non-adjacent form.
         * @param W window size
         * @param precomputes precomputed tables
         * @param n scalar (we don't check here, but should be less than curve order)
         * @param acc accumulator point to add result of multiplication
         * @returns point
         */
        wNAFUnsafe(W, precomputes, n, acc = c.ZERO) {
            const { windows, windowSize } = calcWOpts(W, bits);
            const mask = BigInt(2 ** W - 1); // Create mask with W ones: 0b1111 for W=4 etc.
            const maxNumber = 2 ** W;
            const shiftBy = BigInt(W);
            for (let window = 0; window < windows; window++) {
                const offset = window * windowSize;
                if (n === _0n)
                    break; // No need to go over empty scalar
                // Extract W bits.
                let wbits = Number(n & mask);
                // Shift number by W bits.
                n >>= shiftBy;
                // If the bits are bigger than max size, we'll split those.
                // +224 => 256 - 32
                if (wbits > windowSize) {
                    wbits -= maxNumber;
                    n += _1n;
                }
                if (wbits === 0)
                    continue;
                let curr = precomputes[offset + Math.abs(wbits) - 1]; // -1 because we skip zero
                if (wbits < 0)
                    curr = curr.negate();
                // NOTE: by re-using acc, we can save a lot of additions in case of MSM
                acc = acc.add(curr);
            }
            return acc;
        },
        getPrecomputes(W, P, transform) {
            // Calculate precomputes on a first run, reuse them after
            let comp = pointPrecomputes.get(P);
            if (!comp) {
                comp = this.precomputeWindow(P, W);
                if (W !== 1)
                    pointPrecomputes.set(P, transform(comp));
            }
            return comp;
        },
        wNAFCached(P, n, transform) {
            const W = getW(P);
            return this.wNAF(W, this.getPrecomputes(W, P, transform), n);
        },
        wNAFCachedUnsafe(P, n, transform, prev) {
            const W = getW(P);
            if (W === 1)
                return this.unsafeLadder(P, n, prev); // For W=1 ladder is ~x2 faster
            return this.wNAFUnsafe(W, this.getPrecomputes(W, P, transform), n, prev);
        },
        // We calculate precomputes for elliptic curve point multiplication
        // using windowed method. This specifies window size and
        // stores precomputed values. Usually only base point would be precomputed.
        setWindowSize(P, W) {
            validateW(W, bits);
            pointWindowSizes.set(P, W);
            pointPrecomputes.delete(P);
        },
    };
}
/**
 * Pippenger algorithm for multi-scalar multiplication (MSM, Pa + Qb + Rc + ...).
 * 30x faster vs naive addition on L=4096, 10x faster with precomputes.
 * For N=254bit, L=1, it does: 1024 ADD + 254 DBL. For L=5: 1536 ADD + 254 DBL.
 * Algorithmically constant-time (for same L), even when 1 point + scalar, or when scalar = 0.
 * @param c Curve Point constructor
 * @param fieldN field over CURVE.N - important that it's not over CURVE.P
 * @param points array of L curve points
 * @param scalars array of L scalars (aka private keys / bigints)
 */
function pippenger(c, fieldN, points, scalars) {
    // If we split scalars by some window (let's say 8 bits), every chunk will only
    // take 256 buckets even if there are 4096 scalars, also re-uses double.
    // TODO:
    // - https://eprint.iacr.org/2024/750.pdf
    // - https://tches.iacr.org/index.php/TCHES/article/view/10287
    // 0 is accepted in scalars
    validateMSMPoints(points, c);
    validateMSMScalars(scalars, fieldN);
    if (points.length !== scalars.length)
        throw new Error('arrays of points and scalars must have equal length');
    const zero = c.ZERO;
    const wbits = (0, utils_js_1.bitLen)(BigInt(points.length));
    const windowSize = wbits > 12 ? wbits - 3 : wbits > 4 ? wbits - 2 : wbits ? 2 : 1; // in bits
    const MASK = (1 << windowSize) - 1;
    const buckets = new Array(MASK + 1).fill(zero); // +1 for zero array
    const lastBits = Math.floor((fieldN.BITS - 1) / windowSize) * windowSize;
    let sum = zero;
    for (let i = lastBits; i >= 0; i -= windowSize) {
        buckets.fill(zero);
        for (let j = 0; j < scalars.length; j++) {
            const scalar = scalars[j];
            const wbits = Number((scalar >> BigInt(i)) & BigInt(MASK));
            buckets[wbits] = buckets[wbits].add(points[j]);
        }
        let resI = zero; // not using this will do small speed-up, but will lose ct
        // Skip first bucket, because it is zero
        for (let j = buckets.length - 1, sumI = zero; j > 0; j--) {
            sumI = sumI.add(buckets[j]);
            resI = resI.add(sumI);
        }
        sum = sum.add(resI);
        if (i !== 0)
            for (let j = 0; j < windowSize; j++)
                sum = sum.double();
    }
    return sum;
}
/**
 * Precomputed multi-scalar multiplication (MSM, Pa + Qb + Rc + ...).
 * @param c Curve Point constructor
 * @param fieldN field over CURVE.N - important that it's not over CURVE.P
 * @param points array of L curve points
 * @returns function which multiplies points with scaars
 */
function precomputeMSMUnsafe(c, fieldN, points, windowSize) {
    /**
     * Performance Analysis of Window-based Precomputation
     *
     * Base Case (256-bit scalar, 8-bit window):
     * - Standard precomputation requires:
     *   - 31 additions per scalar × 256 scalars = 7,936 ops
     *   - Plus 255 summary additions = 8,191 total ops
     *   Note: Summary additions can be optimized via accumulator
     *
     * Chunked Precomputation Analysis:
     * - Using 32 chunks requires:
     *   - 255 additions per chunk
     *   - 256 doublings
     *   - Total: (255 × 32) + 256 = 8,416 ops
     *
     * Memory Usage Comparison:
     * Window Size | Standard Points | Chunked Points
     * ------------|-----------------|---------------
     *     4-bit   |     520         |      15
     *     8-bit   |    4,224        |     255
     *    10-bit   |   13,824        |   1,023
     *    16-bit   |  557,056        |  65,535
     *
     * Key Advantages:
     * 1. Enables larger window sizes due to reduced memory overhead
     * 2. More efficient for smaller scalar counts:
     *    - 16 chunks: (16 × 255) + 256 = 4,336 ops
     *    - ~2x faster than standard 8,191 ops
     *
     * Limitations:
     * - Not suitable for plain precomputes (requires 256 constant doublings)
     * - Performance degrades with larger scalar counts:
     *   - Optimal for ~256 scalars
     *   - Less efficient for 4096+ scalars (Pippenger preferred)
     */
    validateW(windowSize, fieldN.BITS);
    validateMSMPoints(points, c);
    const zero = c.ZERO;
    const tableSize = 2 ** windowSize - 1; // table size (without zero)
    const chunks = Math.ceil(fieldN.BITS / windowSize); // chunks of item
    const MASK = BigInt((1 << windowSize) - 1);
    const tables = points.map((p) => {
        const res = [];
        for (let i = 0, acc = p; i < tableSize; i++) {
            res.push(acc);
            acc = acc.add(p);
        }
        return res;
    });
    return (scalars) => {
        validateMSMScalars(scalars, fieldN);
        if (scalars.length > points.length)
            throw new Error('array of scalars must be smaller than array of points');
        let res = zero;
        for (let i = 0; i < chunks; i++) {
            // No need to double if accumulator is still zero.
            if (res !== zero)
                for (let j = 0; j < windowSize; j++)
                    res = res.double();
            const shiftBy = BigInt(chunks * windowSize - (i + 1) * windowSize);
            for (let j = 0; j < scalars.length; j++) {
                const n = scalars[j];
                const curr = Number((n >> shiftBy) & MASK);
                if (!curr)
                    continue; // skip zero scalars chunks
                res = res.add(tables[j][curr - 1]);
            }
        }
        return res;
    };
}
function validateBasic(curve) {
    (0, modular_js_1.validateField)(curve.Fp);
    (0, utils_js_1.validateObject)(curve, {
        n: 'bigint',
        h: 'bigint',
        Gx: 'field',
        Gy: 'field',
    }, {
        nBitLength: 'isSafeInteger',
        nByteLength: 'isSafeInteger',
    });
    // Set defaults
    return Object.freeze({
        ...(0, modular_js_1.nLength)(curve.n, curve.nBitLength),
        ...curve,
        ...{ p: curve.Fp.ORDER },
    });
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/edwards.js":
/*!************************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/edwards.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.twistedEdwards = twistedEdwards;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
// Twisted Edwards curve. The formula is: ax² + y² = 1 + dx²y²
const curve_js_1 = __webpack_require__(/*! ./curve.js */ "../../node_modules/@noble/curves/abstract/curve.js");
const modular_js_1 = __webpack_require__(/*! ./modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const ut = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
// Be friendly to bad ECMAScript parsers by not using bigint literals
// prettier-ignore
const _0n = BigInt(0), _1n = BigInt(1), _2n = BigInt(2), _8n = BigInt(8);
// verification rule is either zip215 or rfc8032 / nist186-5. Consult fromHex:
const VERIFY_DEFAULT = { zip215: true };
function validateOpts(curve) {
    const opts = (0, curve_js_1.validateBasic)(curve);
    ut.validateObject(curve, {
        hash: 'function',
        a: 'bigint',
        d: 'bigint',
        randomBytes: 'function',
    }, {
        adjustScalarBytes: 'function',
        domain: 'function',
        uvRatio: 'function',
        mapToCurve: 'function',
    });
    // Set defaults
    return Object.freeze({ ...opts });
}
/**
 * Creates Twisted Edwards curve with EdDSA signatures.
 * @example
 * import { Field } from '@noble/curves/abstract/modular';
 * // Before that, define BigInt-s: a, d, p, n, Gx, Gy, h
 * const curve = twistedEdwards({ a, d, Fp: Field(p), n, Gx, Gy, h })
 */
function twistedEdwards(curveDef) {
    const CURVE = validateOpts(curveDef);
    const { Fp, n: CURVE_ORDER, prehash: prehash, hash: cHash, randomBytes, nByteLength, h: cofactor, } = CURVE;
    // Important:
    // There are some places where Fp.BYTES is used instead of nByteLength.
    // So far, everything has been tested with curves of Fp.BYTES == nByteLength.
    // TODO: test and find curves which behave otherwise.
    const MASK = _2n << (BigInt(nByteLength * 8) - _1n);
    const modP = Fp.create; // Function overrides
    const Fn = (0, modular_js_1.Field)(CURVE.n, CURVE.nBitLength);
    // sqrt(u/v)
    const uvRatio = CURVE.uvRatio ||
        ((u, v) => {
            try {
                return { isValid: true, value: Fp.sqrt(u * Fp.inv(v)) };
            }
            catch (e) {
                return { isValid: false, value: _0n };
            }
        });
    const adjustScalarBytes = CURVE.adjustScalarBytes || ((bytes) => bytes); // NOOP
    const domain = CURVE.domain ||
        ((data, ctx, phflag) => {
            (0, utils_js_1.abool)('phflag', phflag);
            if (ctx.length || phflag)
                throw new Error('Contexts/pre-hash are not supported');
            return data;
        }); // NOOP
    // 0 <= n < MASK
    // Coordinates larger than Fp.ORDER are allowed for zip215
    function aCoordinate(title, n) {
        ut.aInRange('coordinate ' + title, n, _0n, MASK);
    }
    function assertPoint(other) {
        if (!(other instanceof Point))
            throw new Error('ExtendedPoint expected');
    }
    // Converts Extended point to default (x, y) coordinates.
    // Can accept precomputed Z^-1 - for example, from invertBatch.
    const toAffineMemo = (0, utils_js_1.memoized)((p, iz) => {
        const { ex: x, ey: y, ez: z } = p;
        const is0 = p.is0();
        if (iz == null)
            iz = is0 ? _8n : Fp.inv(z); // 8 was chosen arbitrarily
        const ax = modP(x * iz);
        const ay = modP(y * iz);
        const zz = modP(z * iz);
        if (is0)
            return { x: _0n, y: _1n };
        if (zz !== _1n)
            throw new Error('invZ was invalid');
        return { x: ax, y: ay };
    });
    const assertValidMemo = (0, utils_js_1.memoized)((p) => {
        const { a, d } = CURVE;
        if (p.is0())
            throw new Error('bad point: ZERO'); // TODO: optimize, with vars below?
        // Equation in affine coordinates: ax² + y² = 1 + dx²y²
        // Equation in projective coordinates (X/Z, Y/Z, Z):  (aX² + Y²)Z² = Z⁴ + dX²Y²
        const { ex: X, ey: Y, ez: Z, et: T } = p;
        const X2 = modP(X * X); // X²
        const Y2 = modP(Y * Y); // Y²
        const Z2 = modP(Z * Z); // Z²
        const Z4 = modP(Z2 * Z2); // Z⁴
        const aX2 = modP(X2 * a); // aX²
        const left = modP(Z2 * modP(aX2 + Y2)); // (aX² + Y²)Z²
        const right = modP(Z4 + modP(d * modP(X2 * Y2))); // Z⁴ + dX²Y²
        if (left !== right)
            throw new Error('bad point: equation left != right (1)');
        // In Extended coordinates we also have T, which is x*y=T/Z: check X*Y == Z*T
        const XY = modP(X * Y);
        const ZT = modP(Z * T);
        if (XY !== ZT)
            throw new Error('bad point: equation left != right (2)');
        return true;
    });
    // Extended Point works in extended coordinates: (x, y, z, t) ∋ (x=x/z, y=y/z, t=xy).
    // https://en.wikipedia.org/wiki/Twisted_Edwards_curve#Extended_coordinates
    class Point {
        constructor(ex, ey, ez, et) {
            this.ex = ex;
            this.ey = ey;
            this.ez = ez;
            this.et = et;
            aCoordinate('x', ex);
            aCoordinate('y', ey);
            aCoordinate('z', ez);
            aCoordinate('t', et);
            Object.freeze(this);
        }
        get x() {
            return this.toAffine().x;
        }
        get y() {
            return this.toAffine().y;
        }
        static fromAffine(p) {
            if (p instanceof Point)
                throw new Error('extended point not allowed');
            const { x, y } = p || {};
            aCoordinate('x', x);
            aCoordinate('y', y);
            return new Point(x, y, _1n, modP(x * y));
        }
        static normalizeZ(points) {
            const toInv = Fp.invertBatch(points.map((p) => p.ez));
            return points.map((p, i) => p.toAffine(toInv[i])).map(Point.fromAffine);
        }
        // Multiscalar Multiplication
        static msm(points, scalars) {
            return (0, curve_js_1.pippenger)(Point, Fn, points, scalars);
        }
        // "Private method", don't use it directly
        _setWindowSize(windowSize) {
            wnaf.setWindowSize(this, windowSize);
        }
        // Not required for fromHex(), which always creates valid points.
        // Could be useful for fromAffine().
        assertValidity() {
            assertValidMemo(this);
        }
        // Compare one point to another.
        equals(other) {
            assertPoint(other);
            const { ex: X1, ey: Y1, ez: Z1 } = this;
            const { ex: X2, ey: Y2, ez: Z2 } = other;
            const X1Z2 = modP(X1 * Z2);
            const X2Z1 = modP(X2 * Z1);
            const Y1Z2 = modP(Y1 * Z2);
            const Y2Z1 = modP(Y2 * Z1);
            return X1Z2 === X2Z1 && Y1Z2 === Y2Z1;
        }
        is0() {
            return this.equals(Point.ZERO);
        }
        negate() {
            // Flips point sign to a negative one (-x, y in affine coords)
            return new Point(modP(-this.ex), this.ey, this.ez, modP(-this.et));
        }
        // Fast algo for doubling Extended Point.
        // https://hyperelliptic.org/EFD/g1p/auto-twisted-extended.html#doubling-dbl-2008-hwcd
        // Cost: 4M + 4S + 1*a + 6add + 1*2.
        double() {
            const { a } = CURVE;
            const { ex: X1, ey: Y1, ez: Z1 } = this;
            const A = modP(X1 * X1); // A = X12
            const B = modP(Y1 * Y1); // B = Y12
            const C = modP(_2n * modP(Z1 * Z1)); // C = 2*Z12
            const D = modP(a * A); // D = a*A
            const x1y1 = X1 + Y1;
            const E = modP(modP(x1y1 * x1y1) - A - B); // E = (X1+Y1)2-A-B
            const G = D + B; // G = D+B
            const F = G - C; // F = G-C
            const H = D - B; // H = D-B
            const X3 = modP(E * F); // X3 = E*F
            const Y3 = modP(G * H); // Y3 = G*H
            const T3 = modP(E * H); // T3 = E*H
            const Z3 = modP(F * G); // Z3 = F*G
            return new Point(X3, Y3, Z3, T3);
        }
        // Fast algo for adding 2 Extended Points.
        // https://hyperelliptic.org/EFD/g1p/auto-twisted-extended.html#addition-add-2008-hwcd
        // Cost: 9M + 1*a + 1*d + 7add.
        add(other) {
            assertPoint(other);
            const { a, d } = CURVE;
            const { ex: X1, ey: Y1, ez: Z1, et: T1 } = this;
            const { ex: X2, ey: Y2, ez: Z2, et: T2 } = other;
            // Faster algo for adding 2 Extended Points when curve's a=-1.
            // http://hyperelliptic.org/EFD/g1p/auto-twisted-extended-1.html#addition-add-2008-hwcd-4
            // Cost: 8M + 8add + 2*2.
            // Note: It does not check whether the `other` point is valid.
            if (a === BigInt(-1)) {
                const A = modP((Y1 - X1) * (Y2 + X2));
                const B = modP((Y1 + X1) * (Y2 - X2));
                const F = modP(B - A);
                if (F === _0n)
                    return this.double(); // Same point. Tests say it doesn't affect timing
                const C = modP(Z1 * _2n * T2);
                const D = modP(T1 * _2n * Z2);
                const E = D + C;
                const G = B + A;
                const H = D - C;
                const X3 = modP(E * F);
                const Y3 = modP(G * H);
                const T3 = modP(E * H);
                const Z3 = modP(F * G);
                return new Point(X3, Y3, Z3, T3);
            }
            const A = modP(X1 * X2); // A = X1*X2
            const B = modP(Y1 * Y2); // B = Y1*Y2
            const C = modP(T1 * d * T2); // C = T1*d*T2
            const D = modP(Z1 * Z2); // D = Z1*Z2
            const E = modP((X1 + Y1) * (X2 + Y2) - A - B); // E = (X1+Y1)*(X2+Y2)-A-B
            const F = D - C; // F = D-C
            const G = D + C; // G = D+C
            const H = modP(B - a * A); // H = B-a*A
            const X3 = modP(E * F); // X3 = E*F
            const Y3 = modP(G * H); // Y3 = G*H
            const T3 = modP(E * H); // T3 = E*H
            const Z3 = modP(F * G); // Z3 = F*G
            return new Point(X3, Y3, Z3, T3);
        }
        subtract(other) {
            return this.add(other.negate());
        }
        wNAF(n) {
            return wnaf.wNAFCached(this, n, Point.normalizeZ);
        }
        // Constant-time multiplication.
        multiply(scalar) {
            const n = scalar;
            ut.aInRange('scalar', n, _1n, CURVE_ORDER); // 1 <= scalar < L
            const { p, f } = this.wNAF(n);
            return Point.normalizeZ([p, f])[0];
        }
        // Non-constant-time multiplication. Uses double-and-add algorithm.
        // It's faster, but should only be used when you don't care about
        // an exposed private key e.g. sig verification.
        // Does NOT allow scalars higher than CURVE.n.
        // Accepts optional accumulator to merge with multiply (important for sparse scalars)
        multiplyUnsafe(scalar, acc = Point.ZERO) {
            const n = scalar;
            ut.aInRange('scalar', n, _0n, CURVE_ORDER); // 0 <= scalar < L
            if (n === _0n)
                return I;
            if (this.is0() || n === _1n)
                return this;
            return wnaf.wNAFCachedUnsafe(this, n, Point.normalizeZ, acc);
        }
        // Checks if point is of small order.
        // If you add something to small order point, you will have "dirty"
        // point with torsion component.
        // Multiplies point by cofactor and checks if the result is 0.
        isSmallOrder() {
            return this.multiplyUnsafe(cofactor).is0();
        }
        // Multiplies point by curve order and checks if the result is 0.
        // Returns `false` is the point is dirty.
        isTorsionFree() {
            return wnaf.unsafeLadder(this, CURVE_ORDER).is0();
        }
        // Converts Extended point to default (x, y) coordinates.
        // Can accept precomputed Z^-1 - for example, from invertBatch.
        toAffine(iz) {
            return toAffineMemo(this, iz);
        }
        clearCofactor() {
            const { h: cofactor } = CURVE;
            if (cofactor === _1n)
                return this;
            return this.multiplyUnsafe(cofactor);
        }
        // Converts hash string or Uint8Array to Point.
        // Uses algo from RFC8032 5.1.3.
        static fromHex(hex, zip215 = false) {
            const { d, a } = CURVE;
            const len = Fp.BYTES;
            hex = (0, utils_js_1.ensureBytes)('pointHex', hex, len); // copy hex to a new array
            (0, utils_js_1.abool)('zip215', zip215);
            const normed = hex.slice(); // copy again, we'll manipulate it
            const lastByte = hex[len - 1]; // select last byte
            normed[len - 1] = lastByte & ~0x80; // clear last bit
            const y = ut.bytesToNumberLE(normed);
            // zip215=true is good for consensus-critical apps. =false follows RFC8032 / NIST186-5.
            // RFC8032 prohibits >= p, but ZIP215 doesn't
            // zip215=true:  0 <= y < MASK (2^256 for ed25519)
            // zip215=false: 0 <= y < P (2^255-19 for ed25519)
            const max = zip215 ? MASK : Fp.ORDER;
            ut.aInRange('pointHex.y', y, _0n, max);
            // Ed25519: x² = (y²-1)/(dy²+1) mod p. Ed448: x² = (y²-1)/(dy²-1) mod p. Generic case:
            // ax²+y²=1+dx²y² => y²-1=dx²y²-ax² => y²-1=x²(dy²-a) => x²=(y²-1)/(dy²-a)
            const y2 = modP(y * y); // denominator is always non-0 mod p.
            const u = modP(y2 - _1n); // u = y² - 1
            const v = modP(d * y2 - a); // v = d y² + 1.
            let { isValid, value: x } = uvRatio(u, v); // √(u/v)
            if (!isValid)
                throw new Error('Point.fromHex: invalid y coordinate');
            const isXOdd = (x & _1n) === _1n; // There are 2 square roots. Use x_0 bit to select proper
            const isLastByteOdd = (lastByte & 0x80) !== 0; // x_0, last bit
            if (!zip215 && x === _0n && isLastByteOdd)
                // if x=0 and x_0 = 1, fail
                throw new Error('Point.fromHex: x=0 and x_0=1');
            if (isLastByteOdd !== isXOdd)
                x = modP(-x); // if x_0 != x mod 2, set x = p-x
            return Point.fromAffine({ x, y });
        }
        static fromPrivateKey(privKey) {
            return getExtendedPublicKey(privKey).point;
        }
        toRawBytes() {
            const { x, y } = this.toAffine();
            const bytes = ut.numberToBytesLE(y, Fp.BYTES); // each y has 2 x values (x, -y)
            bytes[bytes.length - 1] |= x & _1n ? 0x80 : 0; // when compressing, it's enough to store y
            return bytes; // and use the last byte to encode sign of x
        }
        toHex() {
            return ut.bytesToHex(this.toRawBytes()); // Same as toRawBytes, but returns string.
        }
    }
    Point.BASE = new Point(CURVE.Gx, CURVE.Gy, _1n, modP(CURVE.Gx * CURVE.Gy));
    Point.ZERO = new Point(_0n, _1n, _1n, _0n); // 0, 1, 1, 0
    const { BASE: G, ZERO: I } = Point;
    const wnaf = (0, curve_js_1.wNAF)(Point, nByteLength * 8);
    function modN(a) {
        return (0, modular_js_1.mod)(a, CURVE_ORDER);
    }
    // Little-endian SHA512 with modulo n
    function modN_LE(hash) {
        return modN(ut.bytesToNumberLE(hash));
    }
    /** Convenience method that creates public key and other stuff. RFC8032 5.1.5 */
    function getExtendedPublicKey(key) {
        const len = Fp.BYTES;
        key = (0, utils_js_1.ensureBytes)('private key', key, len);
        // Hash private key with curve's hash function to produce uniformingly random input
        // Check byte lengths: ensure(64, h(ensure(32, key)))
        const hashed = (0, utils_js_1.ensureBytes)('hashed private key', cHash(key), 2 * len);
        const head = adjustScalarBytes(hashed.slice(0, len)); // clear first half bits, produce FE
        const prefix = hashed.slice(len, 2 * len); // second half is called key prefix (5.1.6)
        const scalar = modN_LE(head); // The actual private scalar
        const point = G.multiply(scalar); // Point on Edwards curve aka public key
        const pointBytes = point.toRawBytes(); // Uint8Array representation
        return { head, prefix, scalar, point, pointBytes };
    }
    // Calculates EdDSA pub key. RFC8032 5.1.5. Privkey is hashed. Use first half with 3 bits cleared
    function getPublicKey(privKey) {
        return getExtendedPublicKey(privKey).pointBytes;
    }
    // int('LE', SHA512(dom2(F, C) || msgs)) mod N
    function hashDomainToScalar(context = new Uint8Array(), ...msgs) {
        const msg = ut.concatBytes(...msgs);
        return modN_LE(cHash(domain(msg, (0, utils_js_1.ensureBytes)('context', context), !!prehash)));
    }
    /** Signs message with privateKey. RFC8032 5.1.6 */
    function sign(msg, privKey, options = {}) {
        msg = (0, utils_js_1.ensureBytes)('message', msg);
        if (prehash)
            msg = prehash(msg); // for ed25519ph etc.
        const { prefix, scalar, pointBytes } = getExtendedPublicKey(privKey);
        const r = hashDomainToScalar(options.context, prefix, msg); // r = dom2(F, C) || prefix || PH(M)
        const R = G.multiply(r).toRawBytes(); // R = rG
        const k = hashDomainToScalar(options.context, R, pointBytes, msg); // R || A || PH(M)
        const s = modN(r + k * scalar); // S = (r + k * s) mod L
        ut.aInRange('signature.s', s, _0n, CURVE_ORDER); // 0 <= s < l
        const res = ut.concatBytes(R, ut.numberToBytesLE(s, Fp.BYTES));
        return (0, utils_js_1.ensureBytes)('result', res, Fp.BYTES * 2); // 64-byte signature
    }
    const verifyOpts = VERIFY_DEFAULT;
    /**
     * Verifies EdDSA signature against message and public key. RFC8032 5.1.7.
     * An extended group equation is checked.
     */
    function verify(sig, msg, publicKey, options = verifyOpts) {
        const { context, zip215 } = options;
        const len = Fp.BYTES; // Verifies EdDSA signature against message and public key. RFC8032 5.1.7.
        sig = (0, utils_js_1.ensureBytes)('signature', sig, 2 * len); // An extended group equation is checked.
        msg = (0, utils_js_1.ensureBytes)('message', msg);
        publicKey = (0, utils_js_1.ensureBytes)('publicKey', publicKey, len);
        if (zip215 !== undefined)
            (0, utils_js_1.abool)('zip215', zip215);
        if (prehash)
            msg = prehash(msg); // for ed25519ph, etc
        const s = ut.bytesToNumberLE(sig.slice(len, 2 * len));
        let A, R, SB;
        try {
            // zip215=true is good for consensus-critical apps. =false follows RFC8032 / NIST186-5.
            // zip215=true:  0 <= y < MASK (2^256 for ed25519)
            // zip215=false: 0 <= y < P (2^255-19 for ed25519)
            A = Point.fromHex(publicKey, zip215);
            R = Point.fromHex(sig.slice(0, len), zip215);
            SB = G.multiplyUnsafe(s); // 0 <= s < l is done inside
        }
        catch (error) {
            return false;
        }
        if (!zip215 && A.isSmallOrder())
            return false;
        const k = hashDomainToScalar(context, R.toRawBytes(), A.toRawBytes(), msg);
        const RkA = R.add(A.multiplyUnsafe(k));
        // Extended group equation
        // [8][S]B = [8]R + [8][k]A'
        return RkA.subtract(SB).clearCofactor().equals(Point.ZERO);
    }
    G._setWindowSize(8); // Enable precomputes. Slows down first publicKey computation by 20ms.
    const utils = {
        getExtendedPublicKey,
        // ed25519 private keys are uniform 32b. No need to check for modulo bias, like in secp256k1.
        randomPrivateKey: () => randomBytes(Fp.BYTES),
        /**
         * We're doing scalar multiplication (used in getPublicKey etc) with precomputed BASE_POINT
         * values. This slows down first getPublicKey() by milliseconds (see Speed section),
         * but allows to speed-up subsequent getPublicKey() calls up to 20x.
         * @param windowSize 2, 4, 8, 16
         */
        precompute(windowSize = 8, point = Point.BASE) {
            point._setWindowSize(windowSize);
            point.multiply(BigInt(3));
            return point;
        },
    };
    return {
        CURVE,
        getPublicKey,
        sign,
        verify,
        ExtendedPoint: Point,
        utils,
    };
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/hash-to-curve.js":
/*!******************************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/hash-to-curve.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.expand_message_xmd = expand_message_xmd;
exports.expand_message_xof = expand_message_xof;
exports.hash_to_field = hash_to_field;
exports.isogenyMap = isogenyMap;
exports.createHasher = createHasher;
const modular_js_1 = __webpack_require__(/*! ./modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
// Octet Stream to Integer. "spec" implementation of os2ip is 2.5x slower vs bytesToNumberBE.
const os2ip = utils_js_1.bytesToNumberBE;
// Integer to Octet Stream (numberToBytesBE)
function i2osp(value, length) {
    anum(value);
    anum(length);
    if (value < 0 || value >= 1 << (8 * length))
        throw new Error('invalid I2OSP input: ' + value);
    const res = Array.from({ length }).fill(0);
    for (let i = length - 1; i >= 0; i--) {
        res[i] = value & 0xff;
        value >>>= 8;
    }
    return new Uint8Array(res);
}
function strxor(a, b) {
    const arr = new Uint8Array(a.length);
    for (let i = 0; i < a.length; i++) {
        arr[i] = a[i] ^ b[i];
    }
    return arr;
}
function anum(item) {
    if (!Number.isSafeInteger(item))
        throw new Error('number expected');
}
// Produces a uniformly random byte string using a cryptographic hash function H that outputs b bits
// https://www.rfc-editor.org/rfc/rfc9380#section-5.3.1
function expand_message_xmd(msg, DST, lenInBytes, H) {
    (0, utils_js_1.abytes)(msg);
    (0, utils_js_1.abytes)(DST);
    anum(lenInBytes);
    // https://www.rfc-editor.org/rfc/rfc9380#section-5.3.3
    if (DST.length > 255)
        DST = H((0, utils_js_1.concatBytes)((0, utils_js_1.utf8ToBytes)('H2C-OVERSIZE-DST-'), DST));
    const { outputLen: b_in_bytes, blockLen: r_in_bytes } = H;
    const ell = Math.ceil(lenInBytes / b_in_bytes);
    if (lenInBytes > 65535 || ell > 255)
        throw new Error('expand_message_xmd: invalid lenInBytes');
    const DST_prime = (0, utils_js_1.concatBytes)(DST, i2osp(DST.length, 1));
    const Z_pad = i2osp(0, r_in_bytes);
    const l_i_b_str = i2osp(lenInBytes, 2); // len_in_bytes_str
    const b = new Array(ell);
    const b_0 = H((0, utils_js_1.concatBytes)(Z_pad, msg, l_i_b_str, i2osp(0, 1), DST_prime));
    b[0] = H((0, utils_js_1.concatBytes)(b_0, i2osp(1, 1), DST_prime));
    for (let i = 1; i <= ell; i++) {
        const args = [strxor(b_0, b[i - 1]), i2osp(i + 1, 1), DST_prime];
        b[i] = H((0, utils_js_1.concatBytes)(...args));
    }
    const pseudo_random_bytes = (0, utils_js_1.concatBytes)(...b);
    return pseudo_random_bytes.slice(0, lenInBytes);
}
// Produces a uniformly random byte string using an extendable-output function (XOF) H.
// 1. The collision resistance of H MUST be at least k bits.
// 2. H MUST be an XOF that has been proved indifferentiable from
//    a random oracle under a reasonable cryptographic assumption.
// https://www.rfc-editor.org/rfc/rfc9380#section-5.3.2
function expand_message_xof(msg, DST, lenInBytes, k, H) {
    (0, utils_js_1.abytes)(msg);
    (0, utils_js_1.abytes)(DST);
    anum(lenInBytes);
    // https://www.rfc-editor.org/rfc/rfc9380#section-5.3.3
    // DST = H('H2C-OVERSIZE-DST-' || a_very_long_DST, Math.ceil((lenInBytes * k) / 8));
    if (DST.length > 255) {
        const dkLen = Math.ceil((2 * k) / 8);
        DST = H.create({ dkLen }).update((0, utils_js_1.utf8ToBytes)('H2C-OVERSIZE-DST-')).update(DST).digest();
    }
    if (lenInBytes > 65535 || DST.length > 255)
        throw new Error('expand_message_xof: invalid lenInBytes');
    return (H.create({ dkLen: lenInBytes })
        .update(msg)
        .update(i2osp(lenInBytes, 2))
        // 2. DST_prime = DST || I2OSP(len(DST), 1)
        .update(DST)
        .update(i2osp(DST.length, 1))
        .digest());
}
/**
 * Hashes arbitrary-length byte strings to a list of one or more elements of a finite field F
 * https://www.rfc-editor.org/rfc/rfc9380#section-5.2
 * @param msg a byte string containing the message to hash
 * @param count the number of elements of F to output
 * @param options `{DST: string, p: bigint, m: number, k: number, expand: 'xmd' | 'xof', hash: H}`, see above
 * @returns [u_0, ..., u_(count - 1)], a list of field elements.
 */
function hash_to_field(msg, count, options) {
    (0, utils_js_1.validateObject)(options, {
        DST: 'stringOrUint8Array',
        p: 'bigint',
        m: 'isSafeInteger',
        k: 'isSafeInteger',
        hash: 'hash',
    });
    const { p, k, m, hash, expand, DST: _DST } = options;
    (0, utils_js_1.abytes)(msg);
    anum(count);
    const DST = typeof _DST === 'string' ? (0, utils_js_1.utf8ToBytes)(_DST) : _DST;
    const log2p = p.toString(2).length;
    const L = Math.ceil((log2p + k) / 8); // section 5.1 of ietf draft link above
    const len_in_bytes = count * m * L;
    let prb; // pseudo_random_bytes
    if (expand === 'xmd') {
        prb = expand_message_xmd(msg, DST, len_in_bytes, hash);
    }
    else if (expand === 'xof') {
        prb = expand_message_xof(msg, DST, len_in_bytes, k, hash);
    }
    else if (expand === '_internal_pass') {
        // for internal tests only
        prb = msg;
    }
    else {
        throw new Error('expand must be "xmd" or "xof"');
    }
    const u = new Array(count);
    for (let i = 0; i < count; i++) {
        const e = new Array(m);
        for (let j = 0; j < m; j++) {
            const elm_offset = L * (j + i * m);
            const tv = prb.subarray(elm_offset, elm_offset + L);
            e[j] = (0, modular_js_1.mod)(os2ip(tv), p);
        }
        u[i] = e;
    }
    return u;
}
function isogenyMap(field, map) {
    // Make same order as in spec
    const COEFF = map.map((i) => Array.from(i).reverse());
    return (x, y) => {
        const [xNum, xDen, yNum, yDen] = COEFF.map((val) => val.reduce((acc, i) => field.add(field.mul(acc, x), i)));
        x = field.div(xNum, xDen); // xNum / xDen
        y = field.mul(y, field.div(yNum, yDen)); // y * (yNum / yDev)
        return { x, y };
    };
}
function createHasher(Point, mapToCurve, def) {
    if (typeof mapToCurve !== 'function')
        throw new Error('mapToCurve() must be defined');
    return {
        // Encodes byte string to elliptic curve.
        // hash_to_curve from https://www.rfc-editor.org/rfc/rfc9380#section-3
        hashToCurve(msg, options) {
            const u = hash_to_field(msg, 2, { ...def, DST: def.DST, ...options });
            const u0 = Point.fromAffine(mapToCurve(u[0]));
            const u1 = Point.fromAffine(mapToCurve(u[1]));
            const P = u0.add(u1).clearCofactor();
            P.assertValidity();
            return P;
        },
        // Encodes byte string to elliptic curve.
        // encode_to_curve from https://www.rfc-editor.org/rfc/rfc9380#section-3
        encodeToCurve(msg, options) {
            const u = hash_to_field(msg, 1, { ...def, DST: def.encodeDST, ...options });
            const P = Point.fromAffine(mapToCurve(u[0])).clearCofactor();
            P.assertValidity();
            return P;
        },
        // Same as encodeToCurve, but without hash
        mapToCurve(scalars) {
            if (!Array.isArray(scalars))
                throw new Error('mapToCurve: expected array of bigints');
            for (const i of scalars)
                if (typeof i !== 'bigint')
                    throw new Error('mapToCurve: expected array of bigints');
            const P = Point.fromAffine(mapToCurve(scalars)).clearCofactor();
            P.assertValidity();
            return P;
        },
    };
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/modular.js":
/*!************************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/modular.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.isNegativeLE = void 0;
exports.mod = mod;
exports.pow = pow;
exports.pow2 = pow2;
exports.invert = invert;
exports.tonelliShanks = tonelliShanks;
exports.FpSqrt = FpSqrt;
exports.validateField = validateField;
exports.FpPow = FpPow;
exports.FpInvertBatch = FpInvertBatch;
exports.FpDiv = FpDiv;
exports.FpLegendre = FpLegendre;
exports.FpIsSquare = FpIsSquare;
exports.nLength = nLength;
exports.Field = Field;
exports.FpSqrtOdd = FpSqrtOdd;
exports.FpSqrtEven = FpSqrtEven;
exports.hashToPrivateScalar = hashToPrivateScalar;
exports.getFieldBytesLength = getFieldBytesLength;
exports.getMinHashLength = getMinHashLength;
exports.mapHashToField = mapHashToField;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
// Utilities for modular arithmetics and finite fields
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
// prettier-ignore
const _0n = BigInt(0), _1n = BigInt(1), _2n = /* @__PURE__ */ BigInt(2), _3n = /* @__PURE__ */ BigInt(3);
// prettier-ignore
const _4n = /* @__PURE__ */ BigInt(4), _5n = /* @__PURE__ */ BigInt(5), _8n = /* @__PURE__ */ BigInt(8);
// prettier-ignore
const _9n = /* @__PURE__ */ BigInt(9), _16n = /* @__PURE__ */ BigInt(16);
// Calculates a modulo b
function mod(a, b) {
    const result = a % b;
    return result >= _0n ? result : b + result;
}
/**
 * Efficiently raise num to power and do modular division.
 * Unsafe in some contexts: uses ladder, so can expose bigint bits.
 * @example
 * pow(2n, 6n, 11n) // 64n % 11n == 9n
 */
// TODO: use field version && remove
function pow(num, power, modulo) {
    if (power < _0n)
        throw new Error('invalid exponent, negatives unsupported');
    if (modulo <= _0n)
        throw new Error('invalid modulus');
    if (modulo === _1n)
        return _0n;
    let res = _1n;
    while (power > _0n) {
        if (power & _1n)
            res = (res * num) % modulo;
        num = (num * num) % modulo;
        power >>= _1n;
    }
    return res;
}
// Does x ^ (2 ^ power) mod p. pow2(30, 4) == 30 ^ (2 ^ 4)
function pow2(x, power, modulo) {
    let res = x;
    while (power-- > _0n) {
        res *= res;
        res %= modulo;
    }
    return res;
}
// Inverses number over modulo
function invert(number, modulo) {
    if (number === _0n)
        throw new Error('invert: expected non-zero number');
    if (modulo <= _0n)
        throw new Error('invert: expected positive modulus, got ' + modulo);
    // Euclidean GCD https://brilliant.org/wiki/extended-euclidean-algorithm/
    // Fermat's little theorem "CT-like" version inv(n) = n^(m-2) mod m is 30x slower.
    let a = mod(number, modulo);
    let b = modulo;
    // prettier-ignore
    let x = _0n, y = _1n, u = _1n, v = _0n;
    while (a !== _0n) {
        // JIT applies optimization if those two lines follow each other
        const q = b / a;
        const r = b % a;
        const m = x - u * q;
        const n = y - v * q;
        // prettier-ignore
        b = a, a = r, x = u, y = v, u = m, v = n;
    }
    const gcd = b;
    if (gcd !== _1n)
        throw new Error('invert: does not exist');
    return mod(x, modulo);
}
/**
 * Tonelli-Shanks square root search algorithm.
 * 1. https://eprint.iacr.org/2012/685.pdf (page 12)
 * 2. Square Roots from 1; 24, 51, 10 to Dan Shanks
 * Will start an infinite loop if field order P is not prime.
 * @param P field order
 * @returns function that takes field Fp (created from P) and number n
 */
function tonelliShanks(P) {
    // Legendre constant: used to calculate Legendre symbol (a | p),
    // which denotes the value of a^((p-1)/2) (mod p).
    // (a | p) ≡ 1    if a is a square (mod p)
    // (a | p) ≡ -1   if a is not a square (mod p)
    // (a | p) ≡ 0    if a ≡ 0 (mod p)
    const legendreC = (P - _1n) / _2n;
    let Q, S, Z;
    // Step 1: By factoring out powers of 2 from p - 1,
    // find q and s such that p - 1 = q*(2^s) with q odd
    for (Q = P - _1n, S = 0; Q % _2n === _0n; Q /= _2n, S++)
        ;
    // Step 2: Select a non-square z such that (z | p) ≡ -1 and set c ≡ zq
    for (Z = _2n; Z < P && pow(Z, legendreC, P) !== P - _1n; Z++) {
        // Crash instead of infinity loop, we cannot reasonable count until P.
        if (Z > 1000)
            throw new Error('Cannot find square root: likely non-prime P');
    }
    // Fast-path
    if (S === 1) {
        const p1div4 = (P + _1n) / _4n;
        return function tonelliFast(Fp, n) {
            const root = Fp.pow(n, p1div4);
            if (!Fp.eql(Fp.sqr(root), n))
                throw new Error('Cannot find square root');
            return root;
        };
    }
    // Slow-path
    const Q1div2 = (Q + _1n) / _2n;
    return function tonelliSlow(Fp, n) {
        // Step 0: Check that n is indeed a square: (n | p) should not be ≡ -1
        if (Fp.pow(n, legendreC) === Fp.neg(Fp.ONE))
            throw new Error('Cannot find square root');
        let r = S;
        // TODO: will fail at Fp2/etc
        let g = Fp.pow(Fp.mul(Fp.ONE, Z), Q); // will update both x and b
        let x = Fp.pow(n, Q1div2); // first guess at the square root
        let b = Fp.pow(n, Q); // first guess at the fudge factor
        while (!Fp.eql(b, Fp.ONE)) {
            if (Fp.eql(b, Fp.ZERO))
                return Fp.ZERO; // https://en.wikipedia.org/wiki/Tonelli%E2%80%93Shanks_algorithm (4. If t = 0, return r = 0)
            // Find m such b^(2^m)==1
            let m = 1;
            for (let t2 = Fp.sqr(b); m < r; m++) {
                if (Fp.eql(t2, Fp.ONE))
                    break;
                t2 = Fp.sqr(t2); // t2 *= t2
            }
            // NOTE: r-m-1 can be bigger than 32, need to convert to bigint before shift, otherwise there will be overflow
            const ge = Fp.pow(g, _1n << BigInt(r - m - 1)); // ge = 2^(r-m-1)
            g = Fp.sqr(ge); // g = ge * ge
            x = Fp.mul(x, ge); // x *= ge
            b = Fp.mul(b, g); // b *= g
            r = m;
        }
        return x;
    };
}
function FpSqrt(P) {
    // NOTE: different algorithms can give different roots, it is up to user to decide which one they want.
    // For example there is FpSqrtOdd/FpSqrtEven to choice root based on oddness (used for hash-to-curve).
    // P ≡ 3 (mod 4)
    // √n = n^((P+1)/4)
    if (P % _4n === _3n) {
        // Not all roots possible!
        // const ORDER =
        //   0x1a0111ea397fe69a4b1ba7b6434bacd764774b84f38512bf6730d2a0f6b0f6241eabfffeb153ffffb9feffffffffaaabn;
        // const NUM = 72057594037927816n;
        const p1div4 = (P + _1n) / _4n;
        return function sqrt3mod4(Fp, n) {
            const root = Fp.pow(n, p1div4);
            // Throw if root**2 != n
            if (!Fp.eql(Fp.sqr(root), n))
                throw new Error('Cannot find square root');
            return root;
        };
    }
    // Atkin algorithm for q ≡ 5 (mod 8), https://eprint.iacr.org/2012/685.pdf (page 10)
    if (P % _8n === _5n) {
        const c1 = (P - _5n) / _8n;
        return function sqrt5mod8(Fp, n) {
            const n2 = Fp.mul(n, _2n);
            const v = Fp.pow(n2, c1);
            const nv = Fp.mul(n, v);
            const i = Fp.mul(Fp.mul(nv, _2n), v);
            const root = Fp.mul(nv, Fp.sub(i, Fp.ONE));
            if (!Fp.eql(Fp.sqr(root), n))
                throw new Error('Cannot find square root');
            return root;
        };
    }
    // P ≡ 9 (mod 16)
    if (P % _16n === _9n) {
        // NOTE: tonelli is too slow for bls-Fp2 calculations even on start
        // Means we cannot use sqrt for constants at all!
        //
        // const c1 = Fp.sqrt(Fp.negate(Fp.ONE)); //  1. c1 = sqrt(-1) in F, i.e., (c1^2) == -1 in F
        // const c2 = Fp.sqrt(c1);                //  2. c2 = sqrt(c1) in F, i.e., (c2^2) == c1 in F
        // const c3 = Fp.sqrt(Fp.negate(c1));     //  3. c3 = sqrt(-c1) in F, i.e., (c3^2) == -c1 in F
        // const c4 = (P + _7n) / _16n;           //  4. c4 = (q + 7) / 16        # Integer arithmetic
        // sqrt = (x) => {
        //   let tv1 = Fp.pow(x, c4);             //  1. tv1 = x^c4
        //   let tv2 = Fp.mul(c1, tv1);           //  2. tv2 = c1 * tv1
        //   const tv3 = Fp.mul(c2, tv1);         //  3. tv3 = c2 * tv1
        //   let tv4 = Fp.mul(c3, tv1);           //  4. tv4 = c3 * tv1
        //   const e1 = Fp.equals(Fp.square(tv2), x); //  5.  e1 = (tv2^2) == x
        //   const e2 = Fp.equals(Fp.square(tv3), x); //  6.  e2 = (tv3^2) == x
        //   tv1 = Fp.cmov(tv1, tv2, e1); //  7. tv1 = CMOV(tv1, tv2, e1)  # Select tv2 if (tv2^2) == x
        //   tv2 = Fp.cmov(tv4, tv3, e2); //  8. tv2 = CMOV(tv4, tv3, e2)  # Select tv3 if (tv3^2) == x
        //   const e3 = Fp.equals(Fp.square(tv2), x); //  9.  e3 = (tv2^2) == x
        //   return Fp.cmov(tv1, tv2, e3); //  10.  z = CMOV(tv1, tv2, e3)  # Select the sqrt from tv1 and tv2
        // }
    }
    // Other cases: Tonelli-Shanks algorithm
    return tonelliShanks(P);
}
// Little-endian check for first LE bit (last BE bit);
const isNegativeLE = (num, modulo) => (mod(num, modulo) & _1n) === _1n;
exports.isNegativeLE = isNegativeLE;
// prettier-ignore
const FIELD_FIELDS = [
    'create', 'isValid', 'is0', 'neg', 'inv', 'sqrt', 'sqr',
    'eql', 'add', 'sub', 'mul', 'pow', 'div',
    'addN', 'subN', 'mulN', 'sqrN'
];
function validateField(field) {
    const initial = {
        ORDER: 'bigint',
        MASK: 'bigint',
        BYTES: 'isSafeInteger',
        BITS: 'isSafeInteger',
    };
    const opts = FIELD_FIELDS.reduce((map, val) => {
        map[val] = 'function';
        return map;
    }, initial);
    return (0, utils_js_1.validateObject)(field, opts);
}
// Generic field functions
/**
 * Same as `pow` but for Fp: non-constant-time.
 * Unsafe in some contexts: uses ladder, so can expose bigint bits.
 */
function FpPow(f, num, power) {
    // Should have same speed as pow for bigints
    // TODO: benchmark!
    if (power < _0n)
        throw new Error('invalid exponent, negatives unsupported');
    if (power === _0n)
        return f.ONE;
    if (power === _1n)
        return num;
    let p = f.ONE;
    let d = num;
    while (power > _0n) {
        if (power & _1n)
            p = f.mul(p, d);
        d = f.sqr(d);
        power >>= _1n;
    }
    return p;
}
/**
 * Efficiently invert an array of Field elements.
 * `inv(0)` will return `undefined` here: make sure to throw an error.
 */
function FpInvertBatch(f, nums) {
    const tmp = new Array(nums.length);
    // Walk from first to last, multiply them by each other MOD p
    const lastMultiplied = nums.reduce((acc, num, i) => {
        if (f.is0(num))
            return acc;
        tmp[i] = acc;
        return f.mul(acc, num);
    }, f.ONE);
    // Invert last element
    const inverted = f.inv(lastMultiplied);
    // Walk from last to first, multiply them by inverted each other MOD p
    nums.reduceRight((acc, num, i) => {
        if (f.is0(num))
            return acc;
        tmp[i] = f.mul(acc, tmp[i]);
        return f.mul(acc, num);
    }, inverted);
    return tmp;
}
function FpDiv(f, lhs, rhs) {
    return f.mul(lhs, typeof rhs === 'bigint' ? invert(rhs, f.ORDER) : f.inv(rhs));
}
function FpLegendre(order) {
    // (a | p) ≡ 1    if a is a square (mod p), quadratic residue
    // (a | p) ≡ -1   if a is not a square (mod p), quadratic non residue
    // (a | p) ≡ 0    if a ≡ 0 (mod p)
    const legendreConst = (order - _1n) / _2n; // Integer arithmetic
    return (f, x) => f.pow(x, legendreConst);
}
// This function returns True whenever the value x is a square in the field F.
function FpIsSquare(f) {
    const legendre = FpLegendre(f.ORDER);
    return (x) => {
        const p = legendre(f, x);
        return f.eql(p, f.ZERO) || f.eql(p, f.ONE);
    };
}
// CURVE.n lengths
function nLength(n, nBitLength) {
    // Bit size, byte size of CURVE.n
    const _nBitLength = nBitLength !== undefined ? nBitLength : n.toString(2).length;
    const nByteLength = Math.ceil(_nBitLength / 8);
    return { nBitLength: _nBitLength, nByteLength };
}
/**
 * Initializes a finite field over prime. **Non-primes are not supported.**
 * Do not init in loop: slow. Very fragile: always run a benchmark on a change.
 * Major performance optimizations:
 * * a) denormalized operations like mulN instead of mul
 * * b) same object shape: never add or remove keys
 * * c) Object.freeze
 * NOTE: operations don't check 'isValid' for all elements for performance reasons,
 * it is caller responsibility to check this.
 * This is low-level code, please make sure you know what you doing.
 * @param ORDER prime positive bigint
 * @param bitLen how many bits the field consumes
 * @param isLE (def: false) if encoding / decoding should be in little-endian
 * @param redef optional faster redefinitions of sqrt and other methods
 */
function Field(ORDER, bitLen, isLE = false, redef = {}) {
    if (ORDER <= _0n)
        throw new Error('invalid field: expected ORDER > 0, got ' + ORDER);
    const { nBitLength: BITS, nByteLength: BYTES } = nLength(ORDER, bitLen);
    if (BYTES > 2048)
        throw new Error('invalid field: expected ORDER of <= 2048 bytes');
    let sqrtP; // cached sqrtP
    const f = Object.freeze({
        ORDER,
        BITS,
        BYTES,
        MASK: (0, utils_js_1.bitMask)(BITS),
        ZERO: _0n,
        ONE: _1n,
        create: (num) => mod(num, ORDER),
        isValid: (num) => {
            if (typeof num !== 'bigint')
                throw new Error('invalid field element: expected bigint, got ' + typeof num);
            return _0n <= num && num < ORDER; // 0 is valid element, but it's not invertible
        },
        is0: (num) => num === _0n,
        isOdd: (num) => (num & _1n) === _1n,
        neg: (num) => mod(-num, ORDER),
        eql: (lhs, rhs) => lhs === rhs,
        sqr: (num) => mod(num * num, ORDER),
        add: (lhs, rhs) => mod(lhs + rhs, ORDER),
        sub: (lhs, rhs) => mod(lhs - rhs, ORDER),
        mul: (lhs, rhs) => mod(lhs * rhs, ORDER),
        pow: (num, power) => FpPow(f, num, power),
        div: (lhs, rhs) => mod(lhs * invert(rhs, ORDER), ORDER),
        // Same as above, but doesn't normalize
        sqrN: (num) => num * num,
        addN: (lhs, rhs) => lhs + rhs,
        subN: (lhs, rhs) => lhs - rhs,
        mulN: (lhs, rhs) => lhs * rhs,
        inv: (num) => invert(num, ORDER),
        sqrt: redef.sqrt ||
            ((n) => {
                if (!sqrtP)
                    sqrtP = FpSqrt(ORDER);
                return sqrtP(f, n);
            }),
        invertBatch: (lst) => FpInvertBatch(f, lst),
        // TODO: do we really need constant cmov?
        // We don't have const-time bigints anyway, so probably will be not very useful
        cmov: (a, b, c) => (c ? b : a),
        toBytes: (num) => (isLE ? (0, utils_js_1.numberToBytesLE)(num, BYTES) : (0, utils_js_1.numberToBytesBE)(num, BYTES)),
        fromBytes: (bytes) => {
            if (bytes.length !== BYTES)
                throw new Error('Field.fromBytes: expected ' + BYTES + ' bytes, got ' + bytes.length);
            return isLE ? (0, utils_js_1.bytesToNumberLE)(bytes) : (0, utils_js_1.bytesToNumberBE)(bytes);
        },
    });
    return Object.freeze(f);
}
function FpSqrtOdd(Fp, elm) {
    if (!Fp.isOdd)
        throw new Error("Field doesn't have isOdd");
    const root = Fp.sqrt(elm);
    return Fp.isOdd(root) ? root : Fp.neg(root);
}
function FpSqrtEven(Fp, elm) {
    if (!Fp.isOdd)
        throw new Error("Field doesn't have isOdd");
    const root = Fp.sqrt(elm);
    return Fp.isOdd(root) ? Fp.neg(root) : root;
}
/**
 * "Constant-time" private key generation utility.
 * Same as mapKeyToField, but accepts less bytes (40 instead of 48 for 32-byte field).
 * Which makes it slightly more biased, less secure.
 * @deprecated use mapKeyToField instead
 */
function hashToPrivateScalar(hash, groupOrder, isLE = false) {
    hash = (0, utils_js_1.ensureBytes)('privateHash', hash);
    const hashLen = hash.length;
    const minLen = nLength(groupOrder).nByteLength + 8;
    if (minLen < 24 || hashLen < minLen || hashLen > 1024)
        throw new Error('hashToPrivateScalar: expected ' + minLen + '-1024 bytes of input, got ' + hashLen);
    const num = isLE ? (0, utils_js_1.bytesToNumberLE)(hash) : (0, utils_js_1.bytesToNumberBE)(hash);
    return mod(num, groupOrder - _1n) + _1n;
}
/**
 * Returns total number of bytes consumed by the field element.
 * For example, 32 bytes for usual 256-bit weierstrass curve.
 * @param fieldOrder number of field elements, usually CURVE.n
 * @returns byte length of field
 */
function getFieldBytesLength(fieldOrder) {
    if (typeof fieldOrder !== 'bigint')
        throw new Error('field order must be bigint');
    const bitLength = fieldOrder.toString(2).length;
    return Math.ceil(bitLength / 8);
}
/**
 * Returns minimal amount of bytes that can be safely reduced
 * by field order.
 * Should be 2^-128 for 128-bit curve such as P256.
 * @param fieldOrder number of field elements, usually CURVE.n
 * @returns byte length of target hash
 */
function getMinHashLength(fieldOrder) {
    const length = getFieldBytesLength(fieldOrder);
    return length + Math.ceil(length / 2);
}
/**
 * "Constant-time" private key generation utility.
 * Can take (n + n/2) or more bytes of uniform input e.g. from CSPRNG or KDF
 * and convert them into private scalar, with the modulo bias being negligible.
 * Needs at least 48 bytes of input for 32-byte private key.
 * https://research.kudelskisecurity.com/2020/07/28/the-definitive-guide-to-modulo-bias-and-how-to-avoid-it/
 * FIPS 186-5, A.2 https://csrc.nist.gov/publications/detail/fips/186/5/final
 * RFC 9380, https://www.rfc-editor.org/rfc/rfc9380#section-5
 * @param hash hash output from SHA3 or a similar function
 * @param groupOrder size of subgroup - (e.g. secp256k1.CURVE.n)
 * @param isLE interpret hash bytes as LE num
 * @returns valid private scalar
 */
function mapHashToField(key, fieldOrder, isLE = false) {
    const len = key.length;
    const fieldLen = getFieldBytesLength(fieldOrder);
    const minLen = getMinHashLength(fieldOrder);
    // No small numbers: need to understand bias story. No huge numbers: easier to detect JS timings.
    if (len < 16 || len < minLen || len > 1024)
        throw new Error('expected ' + minLen + '-1024 bytes of input, got ' + len);
    const num = isLE ? (0, utils_js_1.bytesToNumberBE)(key) : (0, utils_js_1.bytesToNumberLE)(key);
    // `mod(x, 11)` can sometimes produce 0. `mod(x, 10) + 1` is the same, but no 0
    const reduced = mod(num, fieldOrder - _1n) + _1n;
    return isLE ? (0, utils_js_1.numberToBytesLE)(reduced, fieldLen) : (0, utils_js_1.numberToBytesBE)(reduced, fieldLen);
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/montgomery.js":
/*!***************************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/montgomery.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.montgomery = montgomery;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
const modular_js_1 = __webpack_require__(/*! ./modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
const _0n = BigInt(0);
const _1n = BigInt(1);
function validateOpts(curve) {
    (0, utils_js_1.validateObject)(curve, {
        a: 'bigint',
    }, {
        montgomeryBits: 'isSafeInteger',
        nByteLength: 'isSafeInteger',
        adjustScalarBytes: 'function',
        domain: 'function',
        powPminus2: 'function',
        Gu: 'bigint',
    });
    // Set defaults
    return Object.freeze({ ...curve });
}
// NOTE: not really montgomery curve, just bunch of very specific methods for X25519/X448 (RFC 7748, https://www.rfc-editor.org/rfc/rfc7748)
// Uses only one coordinate instead of two
function montgomery(curveDef) {
    const CURVE = validateOpts(curveDef);
    const { P } = CURVE;
    const modP = (n) => (0, modular_js_1.mod)(n, P);
    const montgomeryBits = CURVE.montgomeryBits;
    const montgomeryBytes = Math.ceil(montgomeryBits / 8);
    const fieldLen = CURVE.nByteLength;
    const adjustScalarBytes = CURVE.adjustScalarBytes || ((bytes) => bytes);
    const powPminus2 = CURVE.powPminus2 || ((x) => (0, modular_js_1.pow)(x, P - BigInt(2), P));
    // cswap from RFC7748. But it is not from RFC7748!
    /*
      cswap(swap, x_2, x_3):
           dummy = mask(swap) AND (x_2 XOR x_3)
           x_2 = x_2 XOR dummy
           x_3 = x_3 XOR dummy
           Return (x_2, x_3)
    Where mask(swap) is the all-1 or all-0 word of the same length as x_2
     and x_3, computed, e.g., as mask(swap) = 0 - swap.
    */
    function cswap(swap, x_2, x_3) {
        const dummy = modP(swap * (x_2 - x_3));
        x_2 = modP(x_2 - dummy);
        x_3 = modP(x_3 + dummy);
        return [x_2, x_3];
    }
    // x25519 from 4
    // The constant a24 is (486662 - 2) / 4 = 121665 for curve25519/X25519
    const a24 = (CURVE.a - BigInt(2)) / BigInt(4);
    /**
     *
     * @param pointU u coordinate (x) on Montgomery Curve 25519
     * @param scalar by which the point would be multiplied
     * @returns new Point on Montgomery curve
     */
    function montgomeryLadder(u, scalar) {
        (0, utils_js_1.aInRange)('u', u, _0n, P);
        (0, utils_js_1.aInRange)('scalar', scalar, _0n, P);
        // Section 5: Implementations MUST accept non-canonical values and process them as
        // if they had been reduced modulo the field prime.
        const k = scalar;
        const x_1 = u;
        let x_2 = _1n;
        let z_2 = _0n;
        let x_3 = u;
        let z_3 = _1n;
        let swap = _0n;
        let sw;
        for (let t = BigInt(montgomeryBits - 1); t >= _0n; t--) {
            const k_t = (k >> t) & _1n;
            swap ^= k_t;
            sw = cswap(swap, x_2, x_3);
            x_2 = sw[0];
            x_3 = sw[1];
            sw = cswap(swap, z_2, z_3);
            z_2 = sw[0];
            z_3 = sw[1];
            swap = k_t;
            const A = x_2 + z_2;
            const AA = modP(A * A);
            const B = x_2 - z_2;
            const BB = modP(B * B);
            const E = AA - BB;
            const C = x_3 + z_3;
            const D = x_3 - z_3;
            const DA = modP(D * A);
            const CB = modP(C * B);
            const dacb = DA + CB;
            const da_cb = DA - CB;
            x_3 = modP(dacb * dacb);
            z_3 = modP(x_1 * modP(da_cb * da_cb));
            x_2 = modP(AA * BB);
            z_2 = modP(E * (AA + modP(a24 * E)));
        }
        // (x_2, x_3) = cswap(swap, x_2, x_3)
        sw = cswap(swap, x_2, x_3);
        x_2 = sw[0];
        x_3 = sw[1];
        // (z_2, z_3) = cswap(swap, z_2, z_3)
        sw = cswap(swap, z_2, z_3);
        z_2 = sw[0];
        z_3 = sw[1];
        // z_2^(p - 2)
        const z2 = powPminus2(z_2);
        // Return x_2 * (z_2^(p - 2))
        return modP(x_2 * z2);
    }
    function encodeUCoordinate(u) {
        return (0, utils_js_1.numberToBytesLE)(modP(u), montgomeryBytes);
    }
    function decodeUCoordinate(uEnc) {
        // Section 5: When receiving such an array, implementations of X25519
        // MUST mask the most significant bit in the final byte.
        const u = (0, utils_js_1.ensureBytes)('u coordinate', uEnc, montgomeryBytes);
        if (fieldLen === 32)
            u[31] &= 127; // 0b0111_1111
        return (0, utils_js_1.bytesToNumberLE)(u);
    }
    function decodeScalar(n) {
        const bytes = (0, utils_js_1.ensureBytes)('scalar', n);
        const len = bytes.length;
        if (len !== montgomeryBytes && len !== fieldLen) {
            let valid = '' + montgomeryBytes + ' or ' + fieldLen;
            throw new Error('invalid scalar, expected ' + valid + ' bytes, got ' + len);
        }
        return (0, utils_js_1.bytesToNumberLE)(adjustScalarBytes(bytes));
    }
    function scalarMult(scalar, u) {
        const pointU = decodeUCoordinate(u);
        const _scalar = decodeScalar(scalar);
        const pu = montgomeryLadder(pointU, _scalar);
        // The result was not contributory
        // https://cr.yp.to/ecdh.html#validate
        if (pu === _0n)
            throw new Error('invalid private or public key received');
        return encodeUCoordinate(pu);
    }
    // Computes public key from private. By doing scalar multiplication of base point.
    const GuBytes = encodeUCoordinate(CURVE.Gu);
    function scalarMultBase(scalar) {
        return scalarMult(scalar, GuBytes);
    }
    return {
        scalarMult,
        scalarMultBase,
        getSharedSecret: (privateKey, publicKey) => scalarMult(privateKey, publicKey),
        getPublicKey: (privateKey) => scalarMultBase(privateKey),
        utils: { randomPrivateKey: () => CURVE.randomBytes(CURVE.nByteLength) },
        GuBytes: GuBytes,
    };
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/utils.js":
/*!**********************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/utils.js ***!
  \**********************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.notImplemented = exports.bitMask = void 0;
exports.isBytes = isBytes;
exports.abytes = abytes;
exports.abool = abool;
exports.bytesToHex = bytesToHex;
exports.numberToHexUnpadded = numberToHexUnpadded;
exports.hexToNumber = hexToNumber;
exports.hexToBytes = hexToBytes;
exports.bytesToNumberBE = bytesToNumberBE;
exports.bytesToNumberLE = bytesToNumberLE;
exports.numberToBytesBE = numberToBytesBE;
exports.numberToBytesLE = numberToBytesLE;
exports.numberToVarBytesBE = numberToVarBytesBE;
exports.ensureBytes = ensureBytes;
exports.concatBytes = concatBytes;
exports.equalBytes = equalBytes;
exports.utf8ToBytes = utf8ToBytes;
exports.inRange = inRange;
exports.aInRange = aInRange;
exports.bitLen = bitLen;
exports.bitGet = bitGet;
exports.bitSet = bitSet;
exports.createHmacDrbg = createHmacDrbg;
exports.validateObject = validateObject;
exports.memoized = memoized;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
// 100 lines of code in the file are duplicated from noble-hashes (utils).
// This is OK: `abstract` directory does not use noble-hashes.
// User may opt-in into using different hashing library. This way, noble-hashes
// won't be included into their bundle.
const _0n = /* @__PURE__ */ BigInt(0);
const _1n = /* @__PURE__ */ BigInt(1);
const _2n = /* @__PURE__ */ BigInt(2);
function isBytes(a) {
    return a instanceof Uint8Array || (ArrayBuffer.isView(a) && a.constructor.name === 'Uint8Array');
}
function abytes(item) {
    if (!isBytes(item))
        throw new Error('Uint8Array expected');
}
function abool(title, value) {
    if (typeof value !== 'boolean')
        throw new Error(title + ' boolean expected, got ' + value);
}
// Array where index 0xf0 (240) is mapped to string 'f0'
const hexes = /* @__PURE__ */ Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));
/**
 * @example bytesToHex(Uint8Array.from([0xca, 0xfe, 0x01, 0x23])) // 'cafe0123'
 */
function bytesToHex(bytes) {
    abytes(bytes);
    // pre-caching improves the speed 6x
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
        hex += hexes[bytes[i]];
    }
    return hex;
}
function numberToHexUnpadded(num) {
    const hex = num.toString(16);
    return hex.length & 1 ? '0' + hex : hex;
}
function hexToNumber(hex) {
    if (typeof hex !== 'string')
        throw new Error('hex string expected, got ' + typeof hex);
    return hex === '' ? _0n : BigInt('0x' + hex); // Big Endian
}
// We use optimized technique to convert hex string to byte array
const asciis = { _0: 48, _9: 57, A: 65, F: 70, a: 97, f: 102 };
function asciiToBase16(ch) {
    if (ch >= asciis._0 && ch <= asciis._9)
        return ch - asciis._0; // '2' => 50-48
    if (ch >= asciis.A && ch <= asciis.F)
        return ch - (asciis.A - 10); // 'B' => 66-(65-10)
    if (ch >= asciis.a && ch <= asciis.f)
        return ch - (asciis.a - 10); // 'b' => 98-(97-10)
    return;
}
/**
 * @example hexToBytes('cafe0123') // Uint8Array.from([0xca, 0xfe, 0x01, 0x23])
 */
function hexToBytes(hex) {
    if (typeof hex !== 'string')
        throw new Error('hex string expected, got ' + typeof hex);
    const hl = hex.length;
    const al = hl / 2;
    if (hl % 2)
        throw new Error('hex string expected, got unpadded hex of length ' + hl);
    const array = new Uint8Array(al);
    for (let ai = 0, hi = 0; ai < al; ai++, hi += 2) {
        const n1 = asciiToBase16(hex.charCodeAt(hi));
        const n2 = asciiToBase16(hex.charCodeAt(hi + 1));
        if (n1 === undefined || n2 === undefined) {
            const char = hex[hi] + hex[hi + 1];
            throw new Error('hex string expected, got non-hex character "' + char + '" at index ' + hi);
        }
        array[ai] = n1 * 16 + n2; // multiply first octet, e.g. 'a3' => 10*16+3 => 160 + 3 => 163
    }
    return array;
}
// BE: Big Endian, LE: Little Endian
function bytesToNumberBE(bytes) {
    return hexToNumber(bytesToHex(bytes));
}
function bytesToNumberLE(bytes) {
    abytes(bytes);
    return hexToNumber(bytesToHex(Uint8Array.from(bytes).reverse()));
}
function numberToBytesBE(n, len) {
    return hexToBytes(n.toString(16).padStart(len * 2, '0'));
}
function numberToBytesLE(n, len) {
    return numberToBytesBE(n, len).reverse();
}
// Unpadded, rarely used
function numberToVarBytesBE(n) {
    return hexToBytes(numberToHexUnpadded(n));
}
/**
 * Takes hex string or Uint8Array, converts to Uint8Array.
 * Validates output length.
 * Will throw error for other types.
 * @param title descriptive title for an error e.g. 'private key'
 * @param hex hex string or Uint8Array
 * @param expectedLength optional, will compare to result array's length
 * @returns
 */
function ensureBytes(title, hex, expectedLength) {
    let res;
    if (typeof hex === 'string') {
        try {
            res = hexToBytes(hex);
        }
        catch (e) {
            throw new Error(title + ' must be hex string or Uint8Array, cause: ' + e);
        }
    }
    else if (isBytes(hex)) {
        // Uint8Array.from() instead of hash.slice() because node.js Buffer
        // is instance of Uint8Array, and its slice() creates **mutable** copy
        res = Uint8Array.from(hex);
    }
    else {
        throw new Error(title + ' must be hex string or Uint8Array');
    }
    const len = res.length;
    if (typeof expectedLength === 'number' && len !== expectedLength)
        throw new Error(title + ' of length ' + expectedLength + ' expected, got ' + len);
    return res;
}
/**
 * Copies several Uint8Arrays into one.
 */
function concatBytes(...arrays) {
    let sum = 0;
    for (let i = 0; i < arrays.length; i++) {
        const a = arrays[i];
        abytes(a);
        sum += a.length;
    }
    const res = new Uint8Array(sum);
    for (let i = 0, pad = 0; i < arrays.length; i++) {
        const a = arrays[i];
        res.set(a, pad);
        pad += a.length;
    }
    return res;
}
// Compares 2 u8a-s in kinda constant time
function equalBytes(a, b) {
    if (a.length !== b.length)
        return false;
    let diff = 0;
    for (let i = 0; i < a.length; i++)
        diff |= a[i] ^ b[i];
    return diff === 0;
}
/**
 * @example utf8ToBytes('abc') // new Uint8Array([97, 98, 99])
 */
function utf8ToBytes(str) {
    if (typeof str !== 'string')
        throw new Error('string expected');
    return new Uint8Array(new TextEncoder().encode(str)); // https://bugzil.la/1681809
}
// Is positive bigint
const isPosBig = (n) => typeof n === 'bigint' && _0n <= n;
function inRange(n, min, max) {
    return isPosBig(n) && isPosBig(min) && isPosBig(max) && min <= n && n < max;
}
/**
 * Asserts min <= n < max. NOTE: It's < max and not <= max.
 * @example
 * aInRange('x', x, 1n, 256n); // would assume x is in (1n..255n)
 */
function aInRange(title, n, min, max) {
    // Why min <= n < max and not a (min < n < max) OR b (min <= n <= max)?
    // consider P=256n, min=0n, max=P
    // - a for min=0 would require -1:          `inRange('x', x, -1n, P)`
    // - b would commonly require subtraction:  `inRange('x', x, 0n, P - 1n)`
    // - our way is the cleanest:               `inRange('x', x, 0n, P)
    if (!inRange(n, min, max))
        throw new Error('expected valid ' + title + ': ' + min + ' <= n < ' + max + ', got ' + n);
}
// Bit operations
/**
 * Calculates amount of bits in a bigint.
 * Same as `n.toString(2).length`
 */
function bitLen(n) {
    let len;
    for (len = 0; n > _0n; n >>= _1n, len += 1)
        ;
    return len;
}
/**
 * Gets single bit at position.
 * NOTE: first bit position is 0 (same as arrays)
 * Same as `!!+Array.from(n.toString(2)).reverse()[pos]`
 */
function bitGet(n, pos) {
    return (n >> BigInt(pos)) & _1n;
}
/**
 * Sets single bit at position.
 */
function bitSet(n, pos, value) {
    return n | ((value ? _1n : _0n) << BigInt(pos));
}
/**
 * Calculate mask for N bits. Not using ** operator with bigints because of old engines.
 * Same as BigInt(`0b${Array(i).fill('1').join('')}`)
 */
const bitMask = (n) => (_2n << BigInt(n - 1)) - _1n;
exports.bitMask = bitMask;
// DRBG
const u8n = (data) => new Uint8Array(data); // creates Uint8Array
const u8fr = (arr) => Uint8Array.from(arr); // another shortcut
/**
 * Minimal HMAC-DRBG from NIST 800-90 for RFC6979 sigs.
 * @returns function that will call DRBG until 2nd arg returns something meaningful
 * @example
 *   const drbg = createHmacDRBG<Key>(32, 32, hmac);
 *   drbg(seed, bytesToKey); // bytesToKey must return Key or undefined
 */
function createHmacDrbg(hashLen, qByteLen, hmacFn) {
    if (typeof hashLen !== 'number' || hashLen < 2)
        throw new Error('hashLen must be a number');
    if (typeof qByteLen !== 'number' || qByteLen < 2)
        throw new Error('qByteLen must be a number');
    if (typeof hmacFn !== 'function')
        throw new Error('hmacFn must be a function');
    // Step B, Step C: set hashLen to 8*ceil(hlen/8)
    let v = u8n(hashLen); // Minimal non-full-spec HMAC-DRBG from NIST 800-90 for RFC6979 sigs.
    let k = u8n(hashLen); // Steps B and C of RFC6979 3.2: set hashLen, in our case always same
    let i = 0; // Iterations counter, will throw when over 1000
    const reset = () => {
        v.fill(1);
        k.fill(0);
        i = 0;
    };
    const h = (...b) => hmacFn(k, v, ...b); // hmac(k)(v, ...values)
    const reseed = (seed = u8n()) => {
        // HMAC-DRBG reseed() function. Steps D-G
        k = h(u8fr([0x00]), seed); // k = hmac(k || v || 0x00 || seed)
        v = h(); // v = hmac(k || v)
        if (seed.length === 0)
            return;
        k = h(u8fr([0x01]), seed); // k = hmac(k || v || 0x01 || seed)
        v = h(); // v = hmac(k || v)
    };
    const gen = () => {
        // HMAC-DRBG generate() function
        if (i++ >= 1000)
            throw new Error('drbg: tried 1000 values');
        let len = 0;
        const out = [];
        while (len < qByteLen) {
            v = h();
            const sl = v.slice();
            out.push(sl);
            len += v.length;
        }
        return concatBytes(...out);
    };
    const genUntil = (seed, pred) => {
        reset();
        reseed(seed); // Steps D-G
        let res = undefined; // Step H: grind until k is in [1..n-1]
        while (!(res = pred(gen())))
            reseed();
        reset();
        return res;
    };
    return genUntil;
}
// Validating curves and fields
const validatorFns = {
    bigint: (val) => typeof val === 'bigint',
    function: (val) => typeof val === 'function',
    boolean: (val) => typeof val === 'boolean',
    string: (val) => typeof val === 'string',
    stringOrUint8Array: (val) => typeof val === 'string' || isBytes(val),
    isSafeInteger: (val) => Number.isSafeInteger(val),
    array: (val) => Array.isArray(val),
    field: (val, object) => object.Fp.isValid(val),
    hash: (val) => typeof val === 'function' && Number.isSafeInteger(val.outputLen),
};
// type Record<K extends string | number | symbol, T> = { [P in K]: T; }
function validateObject(object, validators, optValidators = {}) {
    const checkField = (fieldName, type, isOptional) => {
        const checkVal = validatorFns[type];
        if (typeof checkVal !== 'function')
            throw new Error('invalid validator function');
        const val = object[fieldName];
        if (isOptional && val === undefined)
            return;
        if (!checkVal(val, object)) {
            throw new Error('param ' + String(fieldName) + ' is invalid. Expected ' + type + ', got ' + val);
        }
    };
    for (const [fieldName, type] of Object.entries(validators))
        checkField(fieldName, type, false);
    for (const [fieldName, type] of Object.entries(optValidators))
        checkField(fieldName, type, true);
    return object;
}
// validate type tests
// const o: { a: number; b: number; c: number } = { a: 1, b: 5, c: 6 };
// const z0 = validateObject(o, { a: 'isSafeInteger' }, { c: 'bigint' }); // Ok!
// // Should fail type-check
// const z1 = validateObject(o, { a: 'tmp' }, { c: 'zz' });
// const z2 = validateObject(o, { a: 'isSafeInteger' }, { c: 'zz' });
// const z3 = validateObject(o, { test: 'boolean', z: 'bug' });
// const z4 = validateObject(o, { a: 'boolean', z: 'bug' });
/**
 * throws not implemented error
 */
const notImplemented = () => {
    throw new Error('not implemented');
};
exports.notImplemented = notImplemented;
/**
 * Memoizes (caches) computation result.
 * Uses WeakMap: the value is going auto-cleaned by GC after last reference is removed.
 */
function memoized(fn) {
    const map = new WeakMap();
    return (arg, ...args) => {
        const val = map.get(arg);
        if (val !== undefined)
            return val;
        const computed = fn(arg, ...args);
        map.set(arg, computed);
        return computed;
    };
}


/***/ }),

/***/ "../../node_modules/@noble/curves/abstract/weierstrass.js":
/*!****************************************************************!*\
  !*** ../../node_modules/@noble/curves/abstract/weierstrass.js ***!
  \****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.DER = void 0;
exports.weierstrassPoints = weierstrassPoints;
exports.weierstrass = weierstrass;
exports.SWUFpSqrtRatio = SWUFpSqrtRatio;
exports.mapToCurveSimpleSWU = mapToCurveSimpleSWU;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
// Short Weierstrass curve. The formula is: y² = x³ + ax + b
const curve_js_1 = __webpack_require__(/*! ./curve.js */ "../../node_modules/@noble/curves/abstract/curve.js");
const mod = __webpack_require__(/*! ./modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const ut = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
function validateSigVerOpts(opts) {
    if (opts.lowS !== undefined)
        (0, utils_js_1.abool)('lowS', opts.lowS);
    if (opts.prehash !== undefined)
        (0, utils_js_1.abool)('prehash', opts.prehash);
}
function validatePointOpts(curve) {
    const opts = (0, curve_js_1.validateBasic)(curve);
    ut.validateObject(opts, {
        a: 'field',
        b: 'field',
    }, {
        allowedPrivateKeyLengths: 'array',
        wrapPrivateKey: 'boolean',
        isTorsionFree: 'function',
        clearCofactor: 'function',
        allowInfinityPoint: 'boolean',
        fromBytes: 'function',
        toBytes: 'function',
    });
    const { endo, Fp, a } = opts;
    if (endo) {
        if (!Fp.eql(a, Fp.ZERO)) {
            throw new Error('invalid endomorphism, can only be defined for Koblitz curves that have a=0');
        }
        if (typeof endo !== 'object' ||
            typeof endo.beta !== 'bigint' ||
            typeof endo.splitScalar !== 'function') {
            throw new Error('invalid endomorphism, expected beta: bigint and splitScalar: function');
        }
    }
    return Object.freeze({ ...opts });
}
const { bytesToNumberBE: b2n, hexToBytes: h2b } = ut;
/**
 * ASN.1 DER encoding utilities. ASN is very complex & fragile. Format:
 *
 *     [0x30 (SEQUENCE), bytelength, 0x02 (INTEGER), intLength, R, 0x02 (INTEGER), intLength, S]
 *
 * Docs: https://letsencrypt.org/docs/a-warm-welcome-to-asn1-and-der/, https://luca.ntop.org/Teaching/Appunti/asn1.html
 */
exports.DER = {
    // asn.1 DER encoding utils
    Err: class DERErr extends Error {
        constructor(m = '') {
            super(m);
        }
    },
    // Basic building block is TLV (Tag-Length-Value)
    _tlv: {
        encode: (tag, data) => {
            const { Err: E } = exports.DER;
            if (tag < 0 || tag > 256)
                throw new E('tlv.encode: wrong tag');
            if (data.length & 1)
                throw new E('tlv.encode: unpadded data');
            const dataLen = data.length / 2;
            const len = ut.numberToHexUnpadded(dataLen);
            if ((len.length / 2) & 128)
                throw new E('tlv.encode: long form length too big');
            // length of length with long form flag
            const lenLen = dataLen > 127 ? ut.numberToHexUnpadded((len.length / 2) | 128) : '';
            const t = ut.numberToHexUnpadded(tag);
            return t + lenLen + len + data;
        },
        // v - value, l - left bytes (unparsed)
        decode(tag, data) {
            const { Err: E } = exports.DER;
            let pos = 0;
            if (tag < 0 || tag > 256)
                throw new E('tlv.encode: wrong tag');
            if (data.length < 2 || data[pos++] !== tag)
                throw new E('tlv.decode: wrong tlv');
            const first = data[pos++];
            const isLong = !!(first & 128); // First bit of first length byte is flag for short/long form
            let length = 0;
            if (!isLong)
                length = first;
            else {
                // Long form: [longFlag(1bit), lengthLength(7bit), length (BE)]
                const lenLen = first & 127;
                if (!lenLen)
                    throw new E('tlv.decode(long): indefinite length not supported');
                if (lenLen > 4)
                    throw new E('tlv.decode(long): byte length is too big'); // this will overflow u32 in js
                const lengthBytes = data.subarray(pos, pos + lenLen);
                if (lengthBytes.length !== lenLen)
                    throw new E('tlv.decode: length bytes not complete');
                if (lengthBytes[0] === 0)
                    throw new E('tlv.decode(long): zero leftmost byte');
                for (const b of lengthBytes)
                    length = (length << 8) | b;
                pos += lenLen;
                if (length < 128)
                    throw new E('tlv.decode(long): not minimal encoding');
            }
            const v = data.subarray(pos, pos + length);
            if (v.length !== length)
                throw new E('tlv.decode: wrong value length');
            return { v, l: data.subarray(pos + length) };
        },
    },
    // https://crypto.stackexchange.com/a/57734 Leftmost bit of first byte is 'negative' flag,
    // since we always use positive integers here. It must always be empty:
    // - add zero byte if exists
    // - if next byte doesn't have a flag, leading zero is not allowed (minimal encoding)
    _int: {
        encode(num) {
            const { Err: E } = exports.DER;
            if (num < _0n)
                throw new E('integer: negative integers are not allowed');
            let hex = ut.numberToHexUnpadded(num);
            // Pad with zero byte if negative flag is present
            if (Number.parseInt(hex[0], 16) & 0b1000)
                hex = '00' + hex;
            if (hex.length & 1)
                throw new E('unexpected DER parsing assertion: unpadded hex');
            return hex;
        },
        decode(data) {
            const { Err: E } = exports.DER;
            if (data[0] & 128)
                throw new E('invalid signature integer: negative');
            if (data[0] === 0x00 && !(data[1] & 128))
                throw new E('invalid signature integer: unnecessary leading zero');
            return b2n(data);
        },
    },
    toSig(hex) {
        // parse DER signature
        const { Err: E, _int: int, _tlv: tlv } = exports.DER;
        const data = typeof hex === 'string' ? h2b(hex) : hex;
        ut.abytes(data);
        const { v: seqBytes, l: seqLeftBytes } = tlv.decode(0x30, data);
        if (seqLeftBytes.length)
            throw new E('invalid signature: left bytes after parsing');
        const { v: rBytes, l: rLeftBytes } = tlv.decode(0x02, seqBytes);
        const { v: sBytes, l: sLeftBytes } = tlv.decode(0x02, rLeftBytes);
        if (sLeftBytes.length)
            throw new E('invalid signature: left bytes after parsing');
        return { r: int.decode(rBytes), s: int.decode(sBytes) };
    },
    hexFromSig(sig) {
        const { _tlv: tlv, _int: int } = exports.DER;
        const rs = tlv.encode(0x02, int.encode(sig.r));
        const ss = tlv.encode(0x02, int.encode(sig.s));
        const seq = rs + ss;
        return tlv.encode(0x30, seq);
    },
};
// Be friendly to bad ECMAScript parsers by not using bigint literals
// prettier-ignore
const _0n = BigInt(0), _1n = BigInt(1), _2n = BigInt(2), _3n = BigInt(3), _4n = BigInt(4);
function weierstrassPoints(opts) {
    const CURVE = validatePointOpts(opts);
    const { Fp } = CURVE; // All curves has same field / group length as for now, but they can differ
    const Fn = mod.Field(CURVE.n, CURVE.nBitLength);
    const toBytes = CURVE.toBytes ||
        ((_c, point, _isCompressed) => {
            const a = point.toAffine();
            return ut.concatBytes(Uint8Array.from([0x04]), Fp.toBytes(a.x), Fp.toBytes(a.y));
        });
    const fromBytes = CURVE.fromBytes ||
        ((bytes) => {
            // const head = bytes[0];
            const tail = bytes.subarray(1);
            // if (head !== 0x04) throw new Error('Only non-compressed encoding is supported');
            const x = Fp.fromBytes(tail.subarray(0, Fp.BYTES));
            const y = Fp.fromBytes(tail.subarray(Fp.BYTES, 2 * Fp.BYTES));
            return { x, y };
        });
    /**
     * y² = x³ + ax + b: Short weierstrass curve formula
     * @returns y²
     */
    function weierstrassEquation(x) {
        const { a, b } = CURVE;
        const x2 = Fp.sqr(x); // x * x
        const x3 = Fp.mul(x2, x); // x2 * x
        return Fp.add(Fp.add(x3, Fp.mul(x, a)), b); // x3 + a * x + b
    }
    // Validate whether the passed curve params are valid.
    // We check if curve equation works for generator point.
    // `assertValidity()` won't work: `isTorsionFree()` is not available at this point in bls12-381.
    // ProjectivePoint class has not been initialized yet.
    if (!Fp.eql(Fp.sqr(CURVE.Gy), weierstrassEquation(CURVE.Gx)))
        throw new Error('bad generator point: equation left != right');
    // Valid group elements reside in range 1..n-1
    function isWithinCurveOrder(num) {
        return ut.inRange(num, _1n, CURVE.n);
    }
    // Validates if priv key is valid and converts it to bigint.
    // Supports options allowedPrivateKeyLengths and wrapPrivateKey.
    function normPrivateKeyToScalar(key) {
        const { allowedPrivateKeyLengths: lengths, nByteLength, wrapPrivateKey, n: N } = CURVE;
        if (lengths && typeof key !== 'bigint') {
            if (ut.isBytes(key))
                key = ut.bytesToHex(key);
            // Normalize to hex string, pad. E.g. P521 would norm 130-132 char hex to 132-char bytes
            if (typeof key !== 'string' || !lengths.includes(key.length))
                throw new Error('invalid private key');
            key = key.padStart(nByteLength * 2, '0');
        }
        let num;
        try {
            num =
                typeof key === 'bigint'
                    ? key
                    : ut.bytesToNumberBE((0, utils_js_1.ensureBytes)('private key', key, nByteLength));
        }
        catch (error) {
            throw new Error('invalid private key, expected hex or ' + nByteLength + ' bytes, got ' + typeof key);
        }
        if (wrapPrivateKey)
            num = mod.mod(num, N); // disabled by default, enabled for BLS
        ut.aInRange('private key', num, _1n, N); // num in range [1..N-1]
        return num;
    }
    function assertPrjPoint(other) {
        if (!(other instanceof Point))
            throw new Error('ProjectivePoint expected');
    }
    // Memoized toAffine / validity check. They are heavy. Points are immutable.
    // Converts Projective point to affine (x, y) coordinates.
    // Can accept precomputed Z^-1 - for example, from invertBatch.
    // (x, y, z) ∋ (x=x/z, y=y/z)
    const toAffineMemo = (0, utils_js_1.memoized)((p, iz) => {
        const { px: x, py: y, pz: z } = p;
        // Fast-path for normalized points
        if (Fp.eql(z, Fp.ONE))
            return { x, y };
        const is0 = p.is0();
        // If invZ was 0, we return zero point. However we still want to execute
        // all operations, so we replace invZ with a random number, 1.
        if (iz == null)
            iz = is0 ? Fp.ONE : Fp.inv(z);
        const ax = Fp.mul(x, iz);
        const ay = Fp.mul(y, iz);
        const zz = Fp.mul(z, iz);
        if (is0)
            return { x: Fp.ZERO, y: Fp.ZERO };
        if (!Fp.eql(zz, Fp.ONE))
            throw new Error('invZ was invalid');
        return { x: ax, y: ay };
    });
    // NOTE: on exception this will crash 'cached' and no value will be set.
    // Otherwise true will be return
    const assertValidMemo = (0, utils_js_1.memoized)((p) => {
        if (p.is0()) {
            // (0, 1, 0) aka ZERO is invalid in most contexts.
            // In BLS, ZERO can be serialized, so we allow it.
            // (0, 0, 0) is invalid representation of ZERO.
            if (CURVE.allowInfinityPoint && !Fp.is0(p.py))
                return;
            throw new Error('bad point: ZERO');
        }
        // Some 3rd-party test vectors require different wording between here & `fromCompressedHex`
        const { x, y } = p.toAffine();
        // Check if x, y are valid field elements
        if (!Fp.isValid(x) || !Fp.isValid(y))
            throw new Error('bad point: x or y not FE');
        const left = Fp.sqr(y); // y²
        const right = weierstrassEquation(x); // x³ + ax + b
        if (!Fp.eql(left, right))
            throw new Error('bad point: equation left != right');
        if (!p.isTorsionFree())
            throw new Error('bad point: not in prime-order subgroup');
        return true;
    });
    /**
     * Projective Point works in 3d / projective (homogeneous) coordinates: (x, y, z) ∋ (x=x/z, y=y/z)
     * Default Point works in 2d / affine coordinates: (x, y)
     * We're doing calculations in projective, because its operations don't require costly inversion.
     */
    class Point {
        constructor(px, py, pz) {
            this.px = px;
            this.py = py;
            this.pz = pz;
            if (px == null || !Fp.isValid(px))
                throw new Error('x required');
            if (py == null || !Fp.isValid(py))
                throw new Error('y required');
            if (pz == null || !Fp.isValid(pz))
                throw new Error('z required');
            Object.freeze(this);
        }
        // Does not validate if the point is on-curve.
        // Use fromHex instead, or call assertValidity() later.
        static fromAffine(p) {
            const { x, y } = p || {};
            if (!p || !Fp.isValid(x) || !Fp.isValid(y))
                throw new Error('invalid affine point');
            if (p instanceof Point)
                throw new Error('projective point not allowed');
            const is0 = (i) => Fp.eql(i, Fp.ZERO);
            // fromAffine(x:0, y:0) would produce (x:0, y:0, z:1), but we need (x:0, y:1, z:0)
            if (is0(x) && is0(y))
                return Point.ZERO;
            return new Point(x, y, Fp.ONE);
        }
        get x() {
            return this.toAffine().x;
        }
        get y() {
            return this.toAffine().y;
        }
        /**
         * Takes a bunch of Projective Points but executes only one
         * inversion on all of them. Inversion is very slow operation,
         * so this improves performance massively.
         * Optimization: converts a list of projective points to a list of identical points with Z=1.
         */
        static normalizeZ(points) {
            const toInv = Fp.invertBatch(points.map((p) => p.pz));
            return points.map((p, i) => p.toAffine(toInv[i])).map(Point.fromAffine);
        }
        /**
         * Converts hash string or Uint8Array to Point.
         * @param hex short/long ECDSA hex
         */
        static fromHex(hex) {
            const P = Point.fromAffine(fromBytes((0, utils_js_1.ensureBytes)('pointHex', hex)));
            P.assertValidity();
            return P;
        }
        // Multiplies generator point by privateKey.
        static fromPrivateKey(privateKey) {
            return Point.BASE.multiply(normPrivateKeyToScalar(privateKey));
        }
        // Multiscalar Multiplication
        static msm(points, scalars) {
            return (0, curve_js_1.pippenger)(Point, Fn, points, scalars);
        }
        // "Private method", don't use it directly
        _setWindowSize(windowSize) {
            wnaf.setWindowSize(this, windowSize);
        }
        // A point on curve is valid if it conforms to equation.
        assertValidity() {
            assertValidMemo(this);
        }
        hasEvenY() {
            const { y } = this.toAffine();
            if (Fp.isOdd)
                return !Fp.isOdd(y);
            throw new Error("Field doesn't support isOdd");
        }
        /**
         * Compare one point to another.
         */
        equals(other) {
            assertPrjPoint(other);
            const { px: X1, py: Y1, pz: Z1 } = this;
            const { px: X2, py: Y2, pz: Z2 } = other;
            const U1 = Fp.eql(Fp.mul(X1, Z2), Fp.mul(X2, Z1));
            const U2 = Fp.eql(Fp.mul(Y1, Z2), Fp.mul(Y2, Z1));
            return U1 && U2;
        }
        /**
         * Flips point to one corresponding to (x, -y) in Affine coordinates.
         */
        negate() {
            return new Point(this.px, Fp.neg(this.py), this.pz);
        }
        // Renes-Costello-Batina exception-free doubling formula.
        // There is 30% faster Jacobian formula, but it is not complete.
        // https://eprint.iacr.org/2015/1060, algorithm 3
        // Cost: 8M + 3S + 3*a + 2*b3 + 15add.
        double() {
            const { a, b } = CURVE;
            const b3 = Fp.mul(b, _3n);
            const { px: X1, py: Y1, pz: Z1 } = this;
            let X3 = Fp.ZERO, Y3 = Fp.ZERO, Z3 = Fp.ZERO; // prettier-ignore
            let t0 = Fp.mul(X1, X1); // step 1
            let t1 = Fp.mul(Y1, Y1);
            let t2 = Fp.mul(Z1, Z1);
            let t3 = Fp.mul(X1, Y1);
            t3 = Fp.add(t3, t3); // step 5
            Z3 = Fp.mul(X1, Z1);
            Z3 = Fp.add(Z3, Z3);
            X3 = Fp.mul(a, Z3);
            Y3 = Fp.mul(b3, t2);
            Y3 = Fp.add(X3, Y3); // step 10
            X3 = Fp.sub(t1, Y3);
            Y3 = Fp.add(t1, Y3);
            Y3 = Fp.mul(X3, Y3);
            X3 = Fp.mul(t3, X3);
            Z3 = Fp.mul(b3, Z3); // step 15
            t2 = Fp.mul(a, t2);
            t3 = Fp.sub(t0, t2);
            t3 = Fp.mul(a, t3);
            t3 = Fp.add(t3, Z3);
            Z3 = Fp.add(t0, t0); // step 20
            t0 = Fp.add(Z3, t0);
            t0 = Fp.add(t0, t2);
            t0 = Fp.mul(t0, t3);
            Y3 = Fp.add(Y3, t0);
            t2 = Fp.mul(Y1, Z1); // step 25
            t2 = Fp.add(t2, t2);
            t0 = Fp.mul(t2, t3);
            X3 = Fp.sub(X3, t0);
            Z3 = Fp.mul(t2, t1);
            Z3 = Fp.add(Z3, Z3); // step 30
            Z3 = Fp.add(Z3, Z3);
            return new Point(X3, Y3, Z3);
        }
        // Renes-Costello-Batina exception-free addition formula.
        // There is 30% faster Jacobian formula, but it is not complete.
        // https://eprint.iacr.org/2015/1060, algorithm 1
        // Cost: 12M + 0S + 3*a + 3*b3 + 23add.
        add(other) {
            assertPrjPoint(other);
            const { px: X1, py: Y1, pz: Z1 } = this;
            const { px: X2, py: Y2, pz: Z2 } = other;
            let X3 = Fp.ZERO, Y3 = Fp.ZERO, Z3 = Fp.ZERO; // prettier-ignore
            const a = CURVE.a;
            const b3 = Fp.mul(CURVE.b, _3n);
            let t0 = Fp.mul(X1, X2); // step 1
            let t1 = Fp.mul(Y1, Y2);
            let t2 = Fp.mul(Z1, Z2);
            let t3 = Fp.add(X1, Y1);
            let t4 = Fp.add(X2, Y2); // step 5
            t3 = Fp.mul(t3, t4);
            t4 = Fp.add(t0, t1);
            t3 = Fp.sub(t3, t4);
            t4 = Fp.add(X1, Z1);
            let t5 = Fp.add(X2, Z2); // step 10
            t4 = Fp.mul(t4, t5);
            t5 = Fp.add(t0, t2);
            t4 = Fp.sub(t4, t5);
            t5 = Fp.add(Y1, Z1);
            X3 = Fp.add(Y2, Z2); // step 15
            t5 = Fp.mul(t5, X3);
            X3 = Fp.add(t1, t2);
            t5 = Fp.sub(t5, X3);
            Z3 = Fp.mul(a, t4);
            X3 = Fp.mul(b3, t2); // step 20
            Z3 = Fp.add(X3, Z3);
            X3 = Fp.sub(t1, Z3);
            Z3 = Fp.add(t1, Z3);
            Y3 = Fp.mul(X3, Z3);
            t1 = Fp.add(t0, t0); // step 25
            t1 = Fp.add(t1, t0);
            t2 = Fp.mul(a, t2);
            t4 = Fp.mul(b3, t4);
            t1 = Fp.add(t1, t2);
            t2 = Fp.sub(t0, t2); // step 30
            t2 = Fp.mul(a, t2);
            t4 = Fp.add(t4, t2);
            t0 = Fp.mul(t1, t4);
            Y3 = Fp.add(Y3, t0);
            t0 = Fp.mul(t5, t4); // step 35
            X3 = Fp.mul(t3, X3);
            X3 = Fp.sub(X3, t0);
            t0 = Fp.mul(t3, t1);
            Z3 = Fp.mul(t5, Z3);
            Z3 = Fp.add(Z3, t0); // step 40
            return new Point(X3, Y3, Z3);
        }
        subtract(other) {
            return this.add(other.negate());
        }
        is0() {
            return this.equals(Point.ZERO);
        }
        wNAF(n) {
            return wnaf.wNAFCached(this, n, Point.normalizeZ);
        }
        /**
         * Non-constant-time multiplication. Uses double-and-add algorithm.
         * It's faster, but should only be used when you don't care about
         * an exposed private key e.g. sig verification, which works over *public* keys.
         */
        multiplyUnsafe(sc) {
            const { endo, n: N } = CURVE;
            ut.aInRange('scalar', sc, _0n, N);
            const I = Point.ZERO;
            if (sc === _0n)
                return I;
            if (this.is0() || sc === _1n)
                return this;
            // Case a: no endomorphism. Case b: has precomputes.
            if (!endo || wnaf.hasPrecomputes(this))
                return wnaf.wNAFCachedUnsafe(this, sc, Point.normalizeZ);
            // Case c: endomorphism
            let { k1neg, k1, k2neg, k2 } = endo.splitScalar(sc);
            let k1p = I;
            let k2p = I;
            let d = this;
            while (k1 > _0n || k2 > _0n) {
                if (k1 & _1n)
                    k1p = k1p.add(d);
                if (k2 & _1n)
                    k2p = k2p.add(d);
                d = d.double();
                k1 >>= _1n;
                k2 >>= _1n;
            }
            if (k1neg)
                k1p = k1p.negate();
            if (k2neg)
                k2p = k2p.negate();
            k2p = new Point(Fp.mul(k2p.px, endo.beta), k2p.py, k2p.pz);
            return k1p.add(k2p);
        }
        /**
         * Constant time multiplication.
         * Uses wNAF method. Windowed method may be 10% faster,
         * but takes 2x longer to generate and consumes 2x memory.
         * Uses precomputes when available.
         * Uses endomorphism for Koblitz curves.
         * @param scalar by which the point would be multiplied
         * @returns New point
         */
        multiply(scalar) {
            const { endo, n: N } = CURVE;
            ut.aInRange('scalar', scalar, _1n, N);
            let point, fake; // Fake point is used to const-time mult
            if (endo) {
                const { k1neg, k1, k2neg, k2 } = endo.splitScalar(scalar);
                let { p: k1p, f: f1p } = this.wNAF(k1);
                let { p: k2p, f: f2p } = this.wNAF(k2);
                k1p = wnaf.constTimeNegate(k1neg, k1p);
                k2p = wnaf.constTimeNegate(k2neg, k2p);
                k2p = new Point(Fp.mul(k2p.px, endo.beta), k2p.py, k2p.pz);
                point = k1p.add(k2p);
                fake = f1p.add(f2p);
            }
            else {
                const { p, f } = this.wNAF(scalar);
                point = p;
                fake = f;
            }
            // Normalize `z` for both points, but return only real one
            return Point.normalizeZ([point, fake])[0];
        }
        /**
         * Efficiently calculate `aP + bQ`. Unsafe, can expose private key, if used incorrectly.
         * Not using Strauss-Shamir trick: precomputation tables are faster.
         * The trick could be useful if both P and Q are not G (not in our case).
         * @returns non-zero affine point
         */
        multiplyAndAddUnsafe(Q, a, b) {
            const G = Point.BASE; // No Strauss-Shamir trick: we have 10% faster G precomputes
            const mul = (P, a // Select faster multiply() method
            ) => (a === _0n || a === _1n || !P.equals(G) ? P.multiplyUnsafe(a) : P.multiply(a));
            const sum = mul(this, a).add(mul(Q, b));
            return sum.is0() ? undefined : sum;
        }
        // Converts Projective point to affine (x, y) coordinates.
        // Can accept precomputed Z^-1 - for example, from invertBatch.
        // (x, y, z) ∋ (x=x/z, y=y/z)
        toAffine(iz) {
            return toAffineMemo(this, iz);
        }
        isTorsionFree() {
            const { h: cofactor, isTorsionFree } = CURVE;
            if (cofactor === _1n)
                return true; // No subgroups, always torsion-free
            if (isTorsionFree)
                return isTorsionFree(Point, this);
            throw new Error('isTorsionFree() has not been declared for the elliptic curve');
        }
        clearCofactor() {
            const { h: cofactor, clearCofactor } = CURVE;
            if (cofactor === _1n)
                return this; // Fast-path
            if (clearCofactor)
                return clearCofactor(Point, this);
            return this.multiplyUnsafe(CURVE.h);
        }
        toRawBytes(isCompressed = true) {
            (0, utils_js_1.abool)('isCompressed', isCompressed);
            this.assertValidity();
            return toBytes(Point, this, isCompressed);
        }
        toHex(isCompressed = true) {
            (0, utils_js_1.abool)('isCompressed', isCompressed);
            return ut.bytesToHex(this.toRawBytes(isCompressed));
        }
    }
    Point.BASE = new Point(CURVE.Gx, CURVE.Gy, Fp.ONE);
    Point.ZERO = new Point(Fp.ZERO, Fp.ONE, Fp.ZERO);
    const _bits = CURVE.nBitLength;
    const wnaf = (0, curve_js_1.wNAF)(Point, CURVE.endo ? Math.ceil(_bits / 2) : _bits);
    // Validate if generator point is on curve
    return {
        CURVE,
        ProjectivePoint: Point,
        normPrivateKeyToScalar,
        weierstrassEquation,
        isWithinCurveOrder,
    };
}
function validateOpts(curve) {
    const opts = (0, curve_js_1.validateBasic)(curve);
    ut.validateObject(opts, {
        hash: 'hash',
        hmac: 'function',
        randomBytes: 'function',
    }, {
        bits2int: 'function',
        bits2int_modN: 'function',
        lowS: 'boolean',
    });
    return Object.freeze({ lowS: true, ...opts });
}
/**
 * Creates short weierstrass curve and ECDSA signature methods for it.
 * @example
 * import { Field } from '@noble/curves/abstract/modular';
 * // Before that, define BigInt-s: a, b, p, n, Gx, Gy
 * const curve = weierstrass({ a, b, Fp: Field(p), n, Gx, Gy, h: 1n })
 */
function weierstrass(curveDef) {
    const CURVE = validateOpts(curveDef);
    const { Fp, n: CURVE_ORDER } = CURVE;
    const compressedLen = Fp.BYTES + 1; // e.g. 33 for 32
    const uncompressedLen = 2 * Fp.BYTES + 1; // e.g. 65 for 32
    function modN(a) {
        return mod.mod(a, CURVE_ORDER);
    }
    function invN(a) {
        return mod.invert(a, CURVE_ORDER);
    }
    const { ProjectivePoint: Point, normPrivateKeyToScalar, weierstrassEquation, isWithinCurveOrder, } = weierstrassPoints({
        ...CURVE,
        toBytes(_c, point, isCompressed) {
            const a = point.toAffine();
            const x = Fp.toBytes(a.x);
            const cat = ut.concatBytes;
            (0, utils_js_1.abool)('isCompressed', isCompressed);
            if (isCompressed) {
                return cat(Uint8Array.from([point.hasEvenY() ? 0x02 : 0x03]), x);
            }
            else {
                return cat(Uint8Array.from([0x04]), x, Fp.toBytes(a.y));
            }
        },
        fromBytes(bytes) {
            const len = bytes.length;
            const head = bytes[0];
            const tail = bytes.subarray(1);
            // this.assertValidity() is done inside of fromHex
            if (len === compressedLen && (head === 0x02 || head === 0x03)) {
                const x = ut.bytesToNumberBE(tail);
                if (!ut.inRange(x, _1n, Fp.ORDER))
                    throw new Error('Point is not on curve');
                const y2 = weierstrassEquation(x); // y² = x³ + ax + b
                let y;
                try {
                    y = Fp.sqrt(y2); // y = y² ^ (p+1)/4
                }
                catch (sqrtError) {
                    const suffix = sqrtError instanceof Error ? ': ' + sqrtError.message : '';
                    throw new Error('Point is not on curve' + suffix);
                }
                const isYOdd = (y & _1n) === _1n;
                // ECDSA
                const isHeadOdd = (head & 1) === 1;
                if (isHeadOdd !== isYOdd)
                    y = Fp.neg(y);
                return { x, y };
            }
            else if (len === uncompressedLen && head === 0x04) {
                const x = Fp.fromBytes(tail.subarray(0, Fp.BYTES));
                const y = Fp.fromBytes(tail.subarray(Fp.BYTES, 2 * Fp.BYTES));
                return { x, y };
            }
            else {
                const cl = compressedLen;
                const ul = uncompressedLen;
                throw new Error('invalid Point, expected length of ' + cl + ', or uncompressed ' + ul + ', got ' + len);
            }
        },
    });
    const numToNByteStr = (num) => ut.bytesToHex(ut.numberToBytesBE(num, CURVE.nByteLength));
    function isBiggerThanHalfOrder(number) {
        const HALF = CURVE_ORDER >> _1n;
        return number > HALF;
    }
    function normalizeS(s) {
        return isBiggerThanHalfOrder(s) ? modN(-s) : s;
    }
    // slice bytes num
    const slcNum = (b, from, to) => ut.bytesToNumberBE(b.slice(from, to));
    /**
     * ECDSA signature with its (r, s) properties. Supports DER & compact representations.
     */
    class Signature {
        constructor(r, s, recovery) {
            this.r = r;
            this.s = s;
            this.recovery = recovery;
            this.assertValidity();
        }
        // pair (bytes of r, bytes of s)
        static fromCompact(hex) {
            const l = CURVE.nByteLength;
            hex = (0, utils_js_1.ensureBytes)('compactSignature', hex, l * 2);
            return new Signature(slcNum(hex, 0, l), slcNum(hex, l, 2 * l));
        }
        // DER encoded ECDSA signature
        // https://bitcoin.stackexchange.com/questions/57644/what-are-the-parts-of-a-bitcoin-transaction-input-script
        static fromDER(hex) {
            const { r, s } = exports.DER.toSig((0, utils_js_1.ensureBytes)('DER', hex));
            return new Signature(r, s);
        }
        assertValidity() {
            ut.aInRange('r', this.r, _1n, CURVE_ORDER); // r in [1..N]
            ut.aInRange('s', this.s, _1n, CURVE_ORDER); // s in [1..N]
        }
        addRecoveryBit(recovery) {
            return new Signature(this.r, this.s, recovery);
        }
        recoverPublicKey(msgHash) {
            const { r, s, recovery: rec } = this;
            const h = bits2int_modN((0, utils_js_1.ensureBytes)('msgHash', msgHash)); // Truncate hash
            if (rec == null || ![0, 1, 2, 3].includes(rec))
                throw new Error('recovery id invalid');
            const radj = rec === 2 || rec === 3 ? r + CURVE.n : r;
            if (radj >= Fp.ORDER)
                throw new Error('recovery id 2 or 3 invalid');
            const prefix = (rec & 1) === 0 ? '02' : '03';
            const R = Point.fromHex(prefix + numToNByteStr(radj));
            const ir = invN(radj); // r^-1
            const u1 = modN(-h * ir); // -hr^-1
            const u2 = modN(s * ir); // sr^-1
            const Q = Point.BASE.multiplyAndAddUnsafe(R, u1, u2); // (sr^-1)R-(hr^-1)G = -(hr^-1)G + (sr^-1)
            if (!Q)
                throw new Error('point at infinify'); // unsafe is fine: no priv data leaked
            Q.assertValidity();
            return Q;
        }
        // Signatures should be low-s, to prevent malleability.
        hasHighS() {
            return isBiggerThanHalfOrder(this.s);
        }
        normalizeS() {
            return this.hasHighS() ? new Signature(this.r, modN(-this.s), this.recovery) : this;
        }
        // DER-encoded
        toDERRawBytes() {
            return ut.hexToBytes(this.toDERHex());
        }
        toDERHex() {
            return exports.DER.hexFromSig({ r: this.r, s: this.s });
        }
        // padded bytes of r, then padded bytes of s
        toCompactRawBytes() {
            return ut.hexToBytes(this.toCompactHex());
        }
        toCompactHex() {
            return numToNByteStr(this.r) + numToNByteStr(this.s);
        }
    }
    const utils = {
        isValidPrivateKey(privateKey) {
            try {
                normPrivateKeyToScalar(privateKey);
                return true;
            }
            catch (error) {
                return false;
            }
        },
        normPrivateKeyToScalar: normPrivateKeyToScalar,
        /**
         * Produces cryptographically secure private key from random of size
         * (groupLen + ceil(groupLen / 2)) with modulo bias being negligible.
         */
        randomPrivateKey: () => {
            const length = mod.getMinHashLength(CURVE.n);
            return mod.mapHashToField(CURVE.randomBytes(length), CURVE.n);
        },
        /**
         * Creates precompute table for an arbitrary EC point. Makes point "cached".
         * Allows to massively speed-up `point.multiply(scalar)`.
         * @returns cached point
         * @example
         * const fast = utils.precompute(8, ProjectivePoint.fromHex(someonesPubKey));
         * fast.multiply(privKey); // much faster ECDH now
         */
        precompute(windowSize = 8, point = Point.BASE) {
            point._setWindowSize(windowSize);
            point.multiply(BigInt(3)); // 3 is arbitrary, just need any number here
            return point;
        },
    };
    /**
     * Computes public key for a private key. Checks for validity of the private key.
     * @param privateKey private key
     * @param isCompressed whether to return compact (default), or full key
     * @returns Public key, full when isCompressed=false; short when isCompressed=true
     */
    function getPublicKey(privateKey, isCompressed = true) {
        return Point.fromPrivateKey(privateKey).toRawBytes(isCompressed);
    }
    /**
     * Quick and dirty check for item being public key. Does not validate hex, or being on-curve.
     */
    function isProbPub(item) {
        const arr = ut.isBytes(item);
        const str = typeof item === 'string';
        const len = (arr || str) && item.length;
        if (arr)
            return len === compressedLen || len === uncompressedLen;
        if (str)
            return len === 2 * compressedLen || len === 2 * uncompressedLen;
        if (item instanceof Point)
            return true;
        return false;
    }
    /**
     * ECDH (Elliptic Curve Diffie Hellman).
     * Computes shared public key from private key and public key.
     * Checks: 1) private key validity 2) shared key is on-curve.
     * Does NOT hash the result.
     * @param privateA private key
     * @param publicB different public key
     * @param isCompressed whether to return compact (default), or full key
     * @returns shared public key
     */
    function getSharedSecret(privateA, publicB, isCompressed = true) {
        if (isProbPub(privateA))
            throw new Error('first arg must be private key');
        if (!isProbPub(publicB))
            throw new Error('second arg must be public key');
        const b = Point.fromHex(publicB); // check for being on-curve
        return b.multiply(normPrivateKeyToScalar(privateA)).toRawBytes(isCompressed);
    }
    // RFC6979: ensure ECDSA msg is X bytes and < N. RFC suggests optional truncating via bits2octets.
    // FIPS 186-4 4.6 suggests the leftmost min(nBitLen, outLen) bits, which matches bits2int.
    // bits2int can produce res>N, we can do mod(res, N) since the bitLen is the same.
    // int2octets can't be used; pads small msgs with 0: unacceptatble for trunc as per RFC vectors
    const bits2int = CURVE.bits2int ||
        function (bytes) {
            // Our custom check "just in case"
            if (bytes.length > 8192)
                throw new Error('input is too large');
            // For curves with nBitLength % 8 !== 0: bits2octets(bits2octets(m)) !== bits2octets(m)
            // for some cases, since bytes.length * 8 is not actual bitLength.
            const num = ut.bytesToNumberBE(bytes); // check for == u8 done here
            const delta = bytes.length * 8 - CURVE.nBitLength; // truncate to nBitLength leftmost bits
            return delta > 0 ? num >> BigInt(delta) : num;
        };
    const bits2int_modN = CURVE.bits2int_modN ||
        function (bytes) {
            return modN(bits2int(bytes)); // can't use bytesToNumberBE here
        };
    // NOTE: pads output with zero as per spec
    const ORDER_MASK = ut.bitMask(CURVE.nBitLength);
    /**
     * Converts to bytes. Checks if num in `[0..ORDER_MASK-1]` e.g.: `[0..2^256-1]`.
     */
    function int2octets(num) {
        ut.aInRange('num < 2^' + CURVE.nBitLength, num, _0n, ORDER_MASK);
        // works with order, can have different size than numToField!
        return ut.numberToBytesBE(num, CURVE.nByteLength);
    }
    // Steps A, D of RFC6979 3.2
    // Creates RFC6979 seed; converts msg/privKey to numbers.
    // Used only in sign, not in verify.
    // NOTE: we cannot assume here that msgHash has same amount of bytes as curve order,
    // this will be invalid at least for P521. Also it can be bigger for P224 + SHA256
    function prepSig(msgHash, privateKey, opts = defaultSigOpts) {
        if (['recovered', 'canonical'].some((k) => k in opts))
            throw new Error('sign() legacy options not supported');
        const { hash, randomBytes } = CURVE;
        let { lowS, prehash, extraEntropy: ent } = opts; // generates low-s sigs by default
        if (lowS == null)
            lowS = true; // RFC6979 3.2: we skip step A, because we already provide hash
        msgHash = (0, utils_js_1.ensureBytes)('msgHash', msgHash);
        validateSigVerOpts(opts);
        if (prehash)
            msgHash = (0, utils_js_1.ensureBytes)('prehashed msgHash', hash(msgHash));
        // We can't later call bits2octets, since nested bits2int is broken for curves
        // with nBitLength % 8 !== 0. Because of that, we unwrap it here as int2octets call.
        // const bits2octets = (bits) => int2octets(bits2int_modN(bits))
        const h1int = bits2int_modN(msgHash);
        const d = normPrivateKeyToScalar(privateKey); // validate private key, convert to bigint
        const seedArgs = [int2octets(d), int2octets(h1int)];
        // extraEntropy. RFC6979 3.6: additional k' (optional).
        if (ent != null && ent !== false) {
            // K = HMAC_K(V || 0x00 || int2octets(x) || bits2octets(h1) || k')
            const e = ent === true ? randomBytes(Fp.BYTES) : ent; // generate random bytes OR pass as-is
            seedArgs.push((0, utils_js_1.ensureBytes)('extraEntropy', e)); // check for being bytes
        }
        const seed = ut.concatBytes(...seedArgs); // Step D of RFC6979 3.2
        const m = h1int; // NOTE: no need to call bits2int second time here, it is inside truncateHash!
        // Converts signature params into point w r/s, checks result for validity.
        function k2sig(kBytes) {
            // RFC 6979 Section 3.2, step 3: k = bits2int(T)
            const k = bits2int(kBytes); // Cannot use fields methods, since it is group element
            if (!isWithinCurveOrder(k))
                return; // Important: all mod() calls here must be done over N
            const ik = invN(k); // k^-1 mod n
            const q = Point.BASE.multiply(k).toAffine(); // q = Gk
            const r = modN(q.x); // r = q.x mod n
            if (r === _0n)
                return;
            // Can use scalar blinding b^-1(bm + bdr) where b ∈ [1,q−1] according to
            // https://tches.iacr.org/index.php/TCHES/article/view/7337/6509. We've decided against it:
            // a) dependency on CSPRNG b) 15% slowdown c) doesn't really help since bigints are not CT
            const s = modN(ik * modN(m + r * d)); // Not using blinding here
            if (s === _0n)
                return;
            let recovery = (q.x === r ? 0 : 2) | Number(q.y & _1n); // recovery bit (2 or 3, when q.x > n)
            let normS = s;
            if (lowS && isBiggerThanHalfOrder(s)) {
                normS = normalizeS(s); // if lowS was passed, ensure s is always
                recovery ^= 1; // // in the bottom half of N
            }
            return new Signature(r, normS, recovery); // use normS, not s
        }
        return { seed, k2sig };
    }
    const defaultSigOpts = { lowS: CURVE.lowS, prehash: false };
    const defaultVerOpts = { lowS: CURVE.lowS, prehash: false };
    /**
     * Signs message hash with a private key.
     * ```
     * sign(m, d, k) where
     *   (x, y) = G × k
     *   r = x mod n
     *   s = (m + dr)/k mod n
     * ```
     * @param msgHash NOT message. msg needs to be hashed to `msgHash`, or use `prehash`.
     * @param privKey private key
     * @param opts lowS for non-malleable sigs. extraEntropy for mixing randomness into k. prehash will hash first arg.
     * @returns signature with recovery param
     */
    function sign(msgHash, privKey, opts = defaultSigOpts) {
        const { seed, k2sig } = prepSig(msgHash, privKey, opts); // Steps A, D of RFC6979 3.2.
        const C = CURVE;
        const drbg = ut.createHmacDrbg(C.hash.outputLen, C.nByteLength, C.hmac);
        return drbg(seed, k2sig); // Steps B, C, D, E, F, G
    }
    // Enable precomputes. Slows down first publicKey computation by 20ms.
    Point.BASE._setWindowSize(8);
    // utils.precompute(8, ProjectivePoint.BASE)
    /**
     * Verifies a signature against message hash and public key.
     * Rejects lowS signatures by default: to override,
     * specify option `{lowS: false}`. Implements section 4.1.4 from https://www.secg.org/sec1-v2.pdf:
     *
     * ```
     * verify(r, s, h, P) where
     *   U1 = hs^-1 mod n
     *   U2 = rs^-1 mod n
     *   R = U1⋅G - U2⋅P
     *   mod(R.x, n) == r
     * ```
     */
    function verify(signature, msgHash, publicKey, opts = defaultVerOpts) {
        const sg = signature;
        msgHash = (0, utils_js_1.ensureBytes)('msgHash', msgHash);
        publicKey = (0, utils_js_1.ensureBytes)('publicKey', publicKey);
        const { lowS, prehash, format } = opts;
        // Verify opts, deduce signature format
        validateSigVerOpts(opts);
        if ('strict' in opts)
            throw new Error('options.strict was renamed to lowS');
        if (format !== undefined && format !== 'compact' && format !== 'der')
            throw new Error('format must be compact or der');
        const isHex = typeof sg === 'string' || ut.isBytes(sg);
        const isObj = !isHex &&
            !format &&
            typeof sg === 'object' &&
            sg !== null &&
            typeof sg.r === 'bigint' &&
            typeof sg.s === 'bigint';
        if (!isHex && !isObj)
            throw new Error('invalid signature, expected Uint8Array, hex string or Signature instance');
        let _sig = undefined;
        let P;
        try {
            if (isObj)
                _sig = new Signature(sg.r, sg.s);
            if (isHex) {
                // Signature can be represented in 2 ways: compact (2*nByteLength) & DER (variable-length).
                // Since DER can also be 2*nByteLength bytes, we check for it first.
                try {
                    if (format !== 'compact')
                        _sig = Signature.fromDER(sg);
                }
                catch (derError) {
                    if (!(derError instanceof exports.DER.Err))
                        throw derError;
                }
                if (!_sig && format !== 'der')
                    _sig = Signature.fromCompact(sg);
            }
            P = Point.fromHex(publicKey);
        }
        catch (error) {
            return false;
        }
        if (!_sig)
            return false;
        if (lowS && _sig.hasHighS())
            return false;
        if (prehash)
            msgHash = CURVE.hash(msgHash);
        const { r, s } = _sig;
        const h = bits2int_modN(msgHash); // Cannot use fields methods, since it is group element
        const is = invN(s); // s^-1
        const u1 = modN(h * is); // u1 = hs^-1 mod n
        const u2 = modN(r * is); // u2 = rs^-1 mod n
        const R = Point.BASE.multiplyAndAddUnsafe(P, u1, u2)?.toAffine(); // R = u1⋅G + u2⋅P
        if (!R)
            return false;
        const v = modN(R.x);
        return v === r;
    }
    return {
        CURVE,
        getPublicKey,
        getSharedSecret,
        sign,
        verify,
        ProjectivePoint: Point,
        Signature,
        utils,
    };
}
/**
 * Implementation of the Shallue and van de Woestijne method for any weierstrass curve.
 * TODO: check if there is a way to merge this with uvRatio in Edwards; move to modular.
 * b = True and y = sqrt(u / v) if (u / v) is square in F, and
 * b = False and y = sqrt(Z * (u / v)) otherwise.
 * @param Fp
 * @param Z
 * @returns
 */
function SWUFpSqrtRatio(Fp, Z) {
    // Generic implementation
    const q = Fp.ORDER;
    let l = _0n;
    for (let o = q - _1n; o % _2n === _0n; o /= _2n)
        l += _1n;
    const c1 = l; // 1. c1, the largest integer such that 2^c1 divides q - 1.
    // We need 2n ** c1 and 2n ** (c1-1). We can't use **; but we can use <<.
    // 2n ** c1 == 2n << (c1-1)
    const _2n_pow_c1_1 = _2n << (c1 - _1n - _1n);
    const _2n_pow_c1 = _2n_pow_c1_1 * _2n;
    const c2 = (q - _1n) / _2n_pow_c1; // 2. c2 = (q - 1) / (2^c1)  # Integer arithmetic
    const c3 = (c2 - _1n) / _2n; // 3. c3 = (c2 - 1) / 2            # Integer arithmetic
    const c4 = _2n_pow_c1 - _1n; // 4. c4 = 2^c1 - 1                # Integer arithmetic
    const c5 = _2n_pow_c1_1; // 5. c5 = 2^(c1 - 1)                  # Integer arithmetic
    const c6 = Fp.pow(Z, c2); // 6. c6 = Z^c2
    const c7 = Fp.pow(Z, (c2 + _1n) / _2n); // 7. c7 = Z^((c2 + 1) / 2)
    let sqrtRatio = (u, v) => {
        let tv1 = c6; // 1. tv1 = c6
        let tv2 = Fp.pow(v, c4); // 2. tv2 = v^c4
        let tv3 = Fp.sqr(tv2); // 3. tv3 = tv2^2
        tv3 = Fp.mul(tv3, v); // 4. tv3 = tv3 * v
        let tv5 = Fp.mul(u, tv3); // 5. tv5 = u * tv3
        tv5 = Fp.pow(tv5, c3); // 6. tv5 = tv5^c3
        tv5 = Fp.mul(tv5, tv2); // 7. tv5 = tv5 * tv2
        tv2 = Fp.mul(tv5, v); // 8. tv2 = tv5 * v
        tv3 = Fp.mul(tv5, u); // 9. tv3 = tv5 * u
        let tv4 = Fp.mul(tv3, tv2); // 10. tv4 = tv3 * tv2
        tv5 = Fp.pow(tv4, c5); // 11. tv5 = tv4^c5
        let isQR = Fp.eql(tv5, Fp.ONE); // 12. isQR = tv5 == 1
        tv2 = Fp.mul(tv3, c7); // 13. tv2 = tv3 * c7
        tv5 = Fp.mul(tv4, tv1); // 14. tv5 = tv4 * tv1
        tv3 = Fp.cmov(tv2, tv3, isQR); // 15. tv3 = CMOV(tv2, tv3, isQR)
        tv4 = Fp.cmov(tv5, tv4, isQR); // 16. tv4 = CMOV(tv5, tv4, isQR)
        // 17. for i in (c1, c1 - 1, ..., 2):
        for (let i = c1; i > _1n; i--) {
            let tv5 = i - _2n; // 18.    tv5 = i - 2
            tv5 = _2n << (tv5 - _1n); // 19.    tv5 = 2^tv5
            let tvv5 = Fp.pow(tv4, tv5); // 20.    tv5 = tv4^tv5
            const e1 = Fp.eql(tvv5, Fp.ONE); // 21.    e1 = tv5 == 1
            tv2 = Fp.mul(tv3, tv1); // 22.    tv2 = tv3 * tv1
            tv1 = Fp.mul(tv1, tv1); // 23.    tv1 = tv1 * tv1
            tvv5 = Fp.mul(tv4, tv1); // 24.    tv5 = tv4 * tv1
            tv3 = Fp.cmov(tv2, tv3, e1); // 25.    tv3 = CMOV(tv2, tv3, e1)
            tv4 = Fp.cmov(tvv5, tv4, e1); // 26.    tv4 = CMOV(tv5, tv4, e1)
        }
        return { isValid: isQR, value: tv3 };
    };
    if (Fp.ORDER % _4n === _3n) {
        // sqrt_ratio_3mod4(u, v)
        const c1 = (Fp.ORDER - _3n) / _4n; // 1. c1 = (q - 3) / 4     # Integer arithmetic
        const c2 = Fp.sqrt(Fp.neg(Z)); // 2. c2 = sqrt(-Z)
        sqrtRatio = (u, v) => {
            let tv1 = Fp.sqr(v); // 1. tv1 = v^2
            const tv2 = Fp.mul(u, v); // 2. tv2 = u * v
            tv1 = Fp.mul(tv1, tv2); // 3. tv1 = tv1 * tv2
            let y1 = Fp.pow(tv1, c1); // 4. y1 = tv1^c1
            y1 = Fp.mul(y1, tv2); // 5. y1 = y1 * tv2
            const y2 = Fp.mul(y1, c2); // 6. y2 = y1 * c2
            const tv3 = Fp.mul(Fp.sqr(y1), v); // 7. tv3 = y1^2; 8. tv3 = tv3 * v
            const isQR = Fp.eql(tv3, u); // 9. isQR = tv3 == u
            let y = Fp.cmov(y2, y1, isQR); // 10. y = CMOV(y2, y1, isQR)
            return { isValid: isQR, value: y }; // 11. return (isQR, y) isQR ? y : y*c2
        };
    }
    // No curves uses that
    // if (Fp.ORDER % _8n === _5n) // sqrt_ratio_5mod8
    return sqrtRatio;
}
/**
 * Simplified Shallue-van de Woestijne-Ulas Method
 * https://www.rfc-editor.org/rfc/rfc9380#section-6.6.2
 */
function mapToCurveSimpleSWU(Fp, opts) {
    mod.validateField(Fp);
    if (!Fp.isValid(opts.A) || !Fp.isValid(opts.B) || !Fp.isValid(opts.Z))
        throw new Error('mapToCurveSimpleSWU: invalid opts');
    const sqrtRatio = SWUFpSqrtRatio(Fp, opts.Z);
    if (!Fp.isOdd)
        throw new Error('Fp.isOdd is not implemented!');
    // Input: u, an element of F.
    // Output: (x, y), a point on E.
    return (u) => {
        // prettier-ignore
        let tv1, tv2, tv3, tv4, tv5, tv6, x, y;
        tv1 = Fp.sqr(u); // 1.  tv1 = u^2
        tv1 = Fp.mul(tv1, opts.Z); // 2.  tv1 = Z * tv1
        tv2 = Fp.sqr(tv1); // 3.  tv2 = tv1^2
        tv2 = Fp.add(tv2, tv1); // 4.  tv2 = tv2 + tv1
        tv3 = Fp.add(tv2, Fp.ONE); // 5.  tv3 = tv2 + 1
        tv3 = Fp.mul(tv3, opts.B); // 6.  tv3 = B * tv3
        tv4 = Fp.cmov(opts.Z, Fp.neg(tv2), !Fp.eql(tv2, Fp.ZERO)); // 7.  tv4 = CMOV(Z, -tv2, tv2 != 0)
        tv4 = Fp.mul(tv4, opts.A); // 8.  tv4 = A * tv4
        tv2 = Fp.sqr(tv3); // 9.  tv2 = tv3^2
        tv6 = Fp.sqr(tv4); // 10. tv6 = tv4^2
        tv5 = Fp.mul(tv6, opts.A); // 11. tv5 = A * tv6
        tv2 = Fp.add(tv2, tv5); // 12. tv2 = tv2 + tv5
        tv2 = Fp.mul(tv2, tv3); // 13. tv2 = tv2 * tv3
        tv6 = Fp.mul(tv6, tv4); // 14. tv6 = tv6 * tv4
        tv5 = Fp.mul(tv6, opts.B); // 15. tv5 = B * tv6
        tv2 = Fp.add(tv2, tv5); // 16. tv2 = tv2 + tv5
        x = Fp.mul(tv1, tv3); // 17.   x = tv1 * tv3
        const { isValid, value } = sqrtRatio(tv2, tv6); // 18. (is_gx1_square, y1) = sqrt_ratio(tv2, tv6)
        y = Fp.mul(tv1, u); // 19.   y = tv1 * u  -> Z * u^3 * y1
        y = Fp.mul(y, value); // 20.   y = y * y1
        x = Fp.cmov(x, tv3, isValid); // 21.   x = CMOV(x, tv3, is_gx1_square)
        y = Fp.cmov(y, value, isValid); // 22.   y = CMOV(y, y1, is_gx1_square)
        const e1 = Fp.isOdd(u) === Fp.isOdd(y); // 23.  e1 = sgn0(u) == sgn0(y)
        y = Fp.cmov(Fp.neg(y), y, e1); // 24.   y = CMOV(-y, y, e1)
        x = Fp.div(x, tv4); // 25.   x = x / tv4
        return { x, y };
    };
}


/***/ }),

/***/ "../../node_modules/@noble/curves/ed25519.js":
/*!***************************************************!*\
  !*** ../../node_modules/@noble/curves/ed25519.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.hash_to_ristretto255 = exports.hashToRistretto255 = exports.RistrettoPoint = exports.encodeToCurve = exports.hashToCurve = exports.edwardsToMontgomery = exports.x25519 = exports.ed25519ph = exports.ed25519ctx = exports.ed25519 = exports.ED25519_TORSION_SUBGROUP = void 0;
exports.edwardsToMontgomeryPub = edwardsToMontgomeryPub;
exports.edwardsToMontgomeryPriv = edwardsToMontgomeryPriv;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
const sha512_1 = __webpack_require__(/*! @noble/hashes/sha512 */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/sha512.js");
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
const edwards_js_1 = __webpack_require__(/*! ./abstract/edwards.js */ "../../node_modules/@noble/curves/abstract/edwards.js");
const hash_to_curve_js_1 = __webpack_require__(/*! ./abstract/hash-to-curve.js */ "../../node_modules/@noble/curves/abstract/hash-to-curve.js");
const modular_js_1 = __webpack_require__(/*! ./abstract/modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const montgomery_js_1 = __webpack_require__(/*! ./abstract/montgomery.js */ "../../node_modules/@noble/curves/abstract/montgomery.js");
const utils_js_1 = __webpack_require__(/*! ./abstract/utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
/**
 * ed25519 Twisted Edwards curve with following addons:
 * - X25519 ECDH
 * - Ristretto cofactor elimination
 * - Elligator hash-to-group / point indistinguishability
 */
const ED25519_P = BigInt('57896044618658097711785492504343953926634992332820282019728792003956564819949');
// √(-1) aka √(a) aka 2^((p-1)/4)
const ED25519_SQRT_M1 = /* @__PURE__ */ BigInt('19681161376707505956807079304988542015446066515923890162744021073123829784752');
// prettier-ignore
const _0n = BigInt(0), _1n = BigInt(1), _2n = BigInt(2), _3n = BigInt(3);
// prettier-ignore
const _5n = BigInt(5), _8n = BigInt(8);
function ed25519_pow_2_252_3(x) {
    // prettier-ignore
    const _10n = BigInt(10), _20n = BigInt(20), _40n = BigInt(40), _80n = BigInt(80);
    const P = ED25519_P;
    const x2 = (x * x) % P;
    const b2 = (x2 * x) % P; // x^3, 11
    const b4 = ((0, modular_js_1.pow2)(b2, _2n, P) * b2) % P; // x^15, 1111
    const b5 = ((0, modular_js_1.pow2)(b4, _1n, P) * x) % P; // x^31
    const b10 = ((0, modular_js_1.pow2)(b5, _5n, P) * b5) % P;
    const b20 = ((0, modular_js_1.pow2)(b10, _10n, P) * b10) % P;
    const b40 = ((0, modular_js_1.pow2)(b20, _20n, P) * b20) % P;
    const b80 = ((0, modular_js_1.pow2)(b40, _40n, P) * b40) % P;
    const b160 = ((0, modular_js_1.pow2)(b80, _80n, P) * b80) % P;
    const b240 = ((0, modular_js_1.pow2)(b160, _80n, P) * b80) % P;
    const b250 = ((0, modular_js_1.pow2)(b240, _10n, P) * b10) % P;
    const pow_p_5_8 = ((0, modular_js_1.pow2)(b250, _2n, P) * x) % P;
    // ^ To pow to (p+3)/8, multiply it by x.
    return { pow_p_5_8, b2 };
}
function adjustScalarBytes(bytes) {
    // Section 5: For X25519, in order to decode 32 random bytes as an integer scalar,
    // set the three least significant bits of the first byte
    bytes[0] &= 248; // 0b1111_1000
    // and the most significant bit of the last to zero,
    bytes[31] &= 127; // 0b0111_1111
    // set the second most significant bit of the last byte to 1
    bytes[31] |= 64; // 0b0100_0000
    return bytes;
}
// sqrt(u/v)
function uvRatio(u, v) {
    const P = ED25519_P;
    const v3 = (0, modular_js_1.mod)(v * v * v, P); // v³
    const v7 = (0, modular_js_1.mod)(v3 * v3 * v, P); // v⁷
    // (p+3)/8 and (p-5)/8
    const pow = ed25519_pow_2_252_3(u * v7).pow_p_5_8;
    let x = (0, modular_js_1.mod)(u * v3 * pow, P); // (uv³)(uv⁷)^(p-5)/8
    const vx2 = (0, modular_js_1.mod)(v * x * x, P); // vx²
    const root1 = x; // First root candidate
    const root2 = (0, modular_js_1.mod)(x * ED25519_SQRT_M1, P); // Second root candidate
    const useRoot1 = vx2 === u; // If vx² = u (mod p), x is a square root
    const useRoot2 = vx2 === (0, modular_js_1.mod)(-u, P); // If vx² = -u, set x <-- x * 2^((p-1)/4)
    const noRoot = vx2 === (0, modular_js_1.mod)(-u * ED25519_SQRT_M1, P); // There is no valid root, vx² = -u√(-1)
    if (useRoot1)
        x = root1;
    if (useRoot2 || noRoot)
        x = root2; // We return root2 anyway, for const-time
    if ((0, modular_js_1.isNegativeLE)(x, P))
        x = (0, modular_js_1.mod)(-x, P);
    return { isValid: useRoot1 || useRoot2, value: x };
}
// Just in case
exports.ED25519_TORSION_SUBGROUP = [
    '0100000000000000000000000000000000000000000000000000000000000000',
    'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac037a',
    '0000000000000000000000000000000000000000000000000000000000000080',
    '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc05',
    'ecffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff7f',
    '26e8958fc2b227b045c3f489f2ef98f0d5dfac05d3c63339b13802886d53fc85',
    '0000000000000000000000000000000000000000000000000000000000000000',
    'c7176a703d4dd84fba3c0b760d10670f2a2053fa2c39ccc64ec7fd7792ac03fa',
];
const Fp = /* @__PURE__ */ (() => (0, modular_js_1.Field)(ED25519_P, undefined, true))();
const ed25519Defaults = /* @__PURE__ */ (() => ({
    // Param: a
    a: BigInt(-1), // Fp.create(-1) is proper; our way still works and is faster
    // d is equal to -121665/121666 over finite field.
    // Negative number is P - number, and division is invert(number, P)
    d: BigInt('37095705934669439343138083508754565189542113879843219016388785533085940283555'),
    // Finite field 𝔽p over which we'll do calculations; 2n**255n - 19n
    Fp,
    // Subgroup order: how many points curve has
    // 2n**252n + 27742317777372353535851937790883648493n;
    n: BigInt('7237005577332262213973186563042994240857116359379907606001950938285454250989'),
    // Cofactor
    h: _8n,
    // Base point (x, y) aka generator point
    Gx: BigInt('15112221349535400772501151409588531511454012693041857206046113283949847762202'),
    Gy: BigInt('46316835694926478169428394003475163141307993866256225615783033603165251855960'),
    hash: sha512_1.sha512,
    randomBytes: utils_1.randomBytes,
    adjustScalarBytes,
    // dom2
    // Ratio of u to v. Allows us to combine inversion and square root. Uses algo from RFC8032 5.1.3.
    // Constant-time, u/√v
    uvRatio,
}))();
/**
 * ed25519 curve with EdDSA signatures.
 */
exports.ed25519 = (() => (0, edwards_js_1.twistedEdwards)(ed25519Defaults))();
function ed25519_domain(data, ctx, phflag) {
    if (ctx.length > 255)
        throw new Error('Context is too big');
    return (0, utils_1.concatBytes)((0, utils_1.utf8ToBytes)('SigEd25519 no Ed25519 collisions'), new Uint8Array([phflag ? 1 : 0, ctx.length]), ctx, data);
}
exports.ed25519ctx = (() => (0, edwards_js_1.twistedEdwards)({
    ...ed25519Defaults,
    domain: ed25519_domain,
}))();
exports.ed25519ph = (() => (0, edwards_js_1.twistedEdwards)(Object.assign({}, ed25519Defaults, {
    domain: ed25519_domain,
    prehash: sha512_1.sha512,
})))();
exports.x25519 = (() => (0, montgomery_js_1.montgomery)({
    P: ED25519_P,
    a: BigInt(486662),
    montgomeryBits: 255, // n is 253 bits
    nByteLength: 32,
    Gu: BigInt(9),
    powPminus2: (x) => {
        const P = ED25519_P;
        // x^(p-2) aka x^(2^255-21)
        const { pow_p_5_8, b2 } = ed25519_pow_2_252_3(x);
        return (0, modular_js_1.mod)((0, modular_js_1.pow2)(pow_p_5_8, _3n, P) * b2, P);
    },
    adjustScalarBytes,
    randomBytes: utils_1.randomBytes,
}))();
/**
 * Converts ed25519 public key to x25519 public key. Uses formula:
 * * `(u, v) = ((1+y)/(1-y), sqrt(-486664)*u/x)`
 * * `(x, y) = (sqrt(-486664)*u/v, (u-1)/(u+1))`
 * @example
 *   const someonesPub = ed25519.getPublicKey(ed25519.utils.randomPrivateKey());
 *   const aPriv = x25519.utils.randomPrivateKey();
 *   x25519.getSharedSecret(aPriv, edwardsToMontgomeryPub(someonesPub))
 */
function edwardsToMontgomeryPub(edwardsPub) {
    const { y } = exports.ed25519.ExtendedPoint.fromHex(edwardsPub);
    const _1n = BigInt(1);
    return Fp.toBytes(Fp.create((_1n + y) * Fp.inv(_1n - y)));
}
exports.edwardsToMontgomery = edwardsToMontgomeryPub; // deprecated
/**
 * Converts ed25519 secret key to x25519 secret key.
 * @example
 *   const someonesPub = x25519.getPublicKey(x25519.utils.randomPrivateKey());
 *   const aPriv = ed25519.utils.randomPrivateKey();
 *   x25519.getSharedSecret(edwardsToMontgomeryPriv(aPriv), someonesPub)
 */
function edwardsToMontgomeryPriv(edwardsPriv) {
    const hashed = ed25519Defaults.hash(edwardsPriv.subarray(0, 32));
    return ed25519Defaults.adjustScalarBytes(hashed).subarray(0, 32);
}
// Hash To Curve Elligator2 Map (NOTE: different from ristretto255 elligator)
// NOTE: very important part is usage of FpSqrtEven for ELL2_C1_EDWARDS, since
// SageMath returns different root first and everything falls apart
const ELL2_C1 = /* @__PURE__ */ (() => (Fp.ORDER + _3n) / _8n)(); // 1. c1 = (q + 3) / 8       # Integer arithmetic
const ELL2_C2 = /* @__PURE__ */ (() => Fp.pow(_2n, ELL2_C1))(); // 2. c2 = 2^c1
const ELL2_C3 = /* @__PURE__ */ (() => Fp.sqrt(Fp.neg(Fp.ONE)))(); // 3. c3 = sqrt(-1)
// prettier-ignore
function map_to_curve_elligator2_curve25519(u) {
    const ELL2_C4 = (Fp.ORDER - _5n) / _8n; // 4. c4 = (q - 5) / 8       # Integer arithmetic
    const ELL2_J = BigInt(486662);
    let tv1 = Fp.sqr(u); //  1.  tv1 = u^2
    tv1 = Fp.mul(tv1, _2n); //  2.  tv1 = 2 * tv1
    let xd = Fp.add(tv1, Fp.ONE); //  3.   xd = tv1 + 1         # Nonzero: -1 is square (mod p), tv1 is not
    let x1n = Fp.neg(ELL2_J); //  4.  x1n = -J              # x1 = x1n / xd = -J / (1 + 2 * u^2)
    let tv2 = Fp.sqr(xd); //  5.  tv2 = xd^2
    let gxd = Fp.mul(tv2, xd); //  6.  gxd = tv2 * xd        # gxd = xd^3
    let gx1 = Fp.mul(tv1, ELL2_J); //  7.  gx1 = J * tv1         # x1n + J * xd
    gx1 = Fp.mul(gx1, x1n); //  8.  gx1 = gx1 * x1n       # x1n^2 + J * x1n * xd
    gx1 = Fp.add(gx1, tv2); //  9.  gx1 = gx1 + tv2       # x1n^2 + J * x1n * xd + xd^2
    gx1 = Fp.mul(gx1, x1n); //  10. gx1 = gx1 * x1n       # x1n^3 + J * x1n^2 * xd + x1n * xd^2
    let tv3 = Fp.sqr(gxd); //  11. tv3 = gxd^2
    tv2 = Fp.sqr(tv3); //  12. tv2 = tv3^2           # gxd^4
    tv3 = Fp.mul(tv3, gxd); //  13. tv3 = tv3 * gxd       # gxd^3
    tv3 = Fp.mul(tv3, gx1); //  14. tv3 = tv3 * gx1       # gx1 * gxd^3
    tv2 = Fp.mul(tv2, tv3); //  15. tv2 = tv2 * tv3       # gx1 * gxd^7
    let y11 = Fp.pow(tv2, ELL2_C4); //  16. y11 = tv2^c4        # (gx1 * gxd^7)^((p - 5) / 8)
    y11 = Fp.mul(y11, tv3); //  17. y11 = y11 * tv3       # gx1*gxd^3*(gx1*gxd^7)^((p-5)/8)
    let y12 = Fp.mul(y11, ELL2_C3); //  18. y12 = y11 * c3
    tv2 = Fp.sqr(y11); //  19. tv2 = y11^2
    tv2 = Fp.mul(tv2, gxd); //  20. tv2 = tv2 * gxd
    let e1 = Fp.eql(tv2, gx1); //  21.  e1 = tv2 == gx1
    let y1 = Fp.cmov(y12, y11, e1); //  22.  y1 = CMOV(y12, y11, e1)  # If g(x1) is square, this is its sqrt
    let x2n = Fp.mul(x1n, tv1); //  23. x2n = x1n * tv1       # x2 = x2n / xd = 2 * u^2 * x1n / xd
    let y21 = Fp.mul(y11, u); //  24. y21 = y11 * u
    y21 = Fp.mul(y21, ELL2_C2); //  25. y21 = y21 * c2
    let y22 = Fp.mul(y21, ELL2_C3); //  26. y22 = y21 * c3
    let gx2 = Fp.mul(gx1, tv1); //  27. gx2 = gx1 * tv1       # g(x2) = gx2 / gxd = 2 * u^2 * g(x1)
    tv2 = Fp.sqr(y21); //  28. tv2 = y21^2
    tv2 = Fp.mul(tv2, gxd); //  29. tv2 = tv2 * gxd
    let e2 = Fp.eql(tv2, gx2); //  30.  e2 = tv2 == gx2
    let y2 = Fp.cmov(y22, y21, e2); //  31.  y2 = CMOV(y22, y21, e2)  # If g(x2) is square, this is its sqrt
    tv2 = Fp.sqr(y1); //  32. tv2 = y1^2
    tv2 = Fp.mul(tv2, gxd); //  33. tv2 = tv2 * gxd
    let e3 = Fp.eql(tv2, gx1); //  34.  e3 = tv2 == gx1
    let xn = Fp.cmov(x2n, x1n, e3); //  35.  xn = CMOV(x2n, x1n, e3)  # If e3, x = x1, else x = x2
    let y = Fp.cmov(y2, y1, e3); //  36.   y = CMOV(y2, y1, e3)    # If e3, y = y1, else y = y2
    let e4 = Fp.isOdd(y); //  37.  e4 = sgn0(y) == 1        # Fix sign of y
    y = Fp.cmov(y, Fp.neg(y), e3 !== e4); //  38.   y = CMOV(y, -y, e3 XOR e4)
    return { xMn: xn, xMd: xd, yMn: y, yMd: _1n }; //  39. return (xn, xd, y, 1)
}
const ELL2_C1_EDWARDS = /* @__PURE__ */ (() => (0, modular_js_1.FpSqrtEven)(Fp, Fp.neg(BigInt(486664))))(); // sgn0(c1) MUST equal 0
function map_to_curve_elligator2_edwards25519(u) {
    const { xMn, xMd, yMn, yMd } = map_to_curve_elligator2_curve25519(u); //  1.  (xMn, xMd, yMn, yMd) =
    // map_to_curve_elligator2_curve25519(u)
    let xn = Fp.mul(xMn, yMd); //  2.  xn = xMn * yMd
    xn = Fp.mul(xn, ELL2_C1_EDWARDS); //  3.  xn = xn * c1
    let xd = Fp.mul(xMd, yMn); //  4.  xd = xMd * yMn    # xn / xd = c1 * xM / yM
    let yn = Fp.sub(xMn, xMd); //  5.  yn = xMn - xMd
    let yd = Fp.add(xMn, xMd); //  6.  yd = xMn + xMd    # (n / d - 1) / (n / d + 1) = (n - d) / (n + d)
    let tv1 = Fp.mul(xd, yd); //  7. tv1 = xd * yd
    let e = Fp.eql(tv1, Fp.ZERO); //  8.   e = tv1 == 0
    xn = Fp.cmov(xn, Fp.ZERO, e); //  9.  xn = CMOV(xn, 0, e)
    xd = Fp.cmov(xd, Fp.ONE, e); //  10. xd = CMOV(xd, 1, e)
    yn = Fp.cmov(yn, Fp.ONE, e); //  11. yn = CMOV(yn, 1, e)
    yd = Fp.cmov(yd, Fp.ONE, e); //  12. yd = CMOV(yd, 1, e)
    const inv = Fp.invertBatch([xd, yd]); // batch division
    return { x: Fp.mul(xn, inv[0]), y: Fp.mul(yn, inv[1]) }; //  13. return (xn, xd, yn, yd)
}
const htf = /* @__PURE__ */ (() => (0, hash_to_curve_js_1.createHasher)(exports.ed25519.ExtendedPoint, (scalars) => map_to_curve_elligator2_edwards25519(scalars[0]), {
    DST: 'edwards25519_XMD:SHA-512_ELL2_RO_',
    encodeDST: 'edwards25519_XMD:SHA-512_ELL2_NU_',
    p: Fp.ORDER,
    m: 1,
    k: 128,
    expand: 'xmd',
    hash: sha512_1.sha512,
}))();
exports.hashToCurve = (() => htf.hashToCurve)();
exports.encodeToCurve = (() => htf.encodeToCurve)();
function assertRstPoint(other) {
    if (!(other instanceof RistPoint))
        throw new Error('RistrettoPoint expected');
}
// √(-1) aka √(a) aka 2^((p-1)/4)
const SQRT_M1 = ED25519_SQRT_M1;
// √(ad - 1)
const SQRT_AD_MINUS_ONE = /* @__PURE__ */ BigInt('25063068953384623474111414158702152701244531502492656460079210482610430750235');
// 1 / √(a-d)
const INVSQRT_A_MINUS_D = /* @__PURE__ */ BigInt('54469307008909316920995813868745141605393597292927456921205312896311721017578');
// 1-d²
const ONE_MINUS_D_SQ = /* @__PURE__ */ BigInt('1159843021668779879193775521855586647937357759715417654439879720876111806838');
// (d-1)²
const D_MINUS_ONE_SQ = /* @__PURE__ */ BigInt('40440834346308536858101042469323190826248399146238708352240133220865137265952');
// Calculates 1/√(number)
const invertSqrt = (number) => uvRatio(_1n, number);
const MAX_255B = /* @__PURE__ */ BigInt('0x7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff');
const bytes255ToNumberLE = (bytes) => exports.ed25519.CURVE.Fp.create((0, utils_js_1.bytesToNumberLE)(bytes) & MAX_255B);
// Computes Elligator map for Ristretto
// https://ristretto.group/formulas/elligator.html
function calcElligatorRistrettoMap(r0) {
    const { d } = exports.ed25519.CURVE;
    const P = exports.ed25519.CURVE.Fp.ORDER;
    const mod = exports.ed25519.CURVE.Fp.create;
    const r = mod(SQRT_M1 * r0 * r0); // 1
    const Ns = mod((r + _1n) * ONE_MINUS_D_SQ); // 2
    let c = BigInt(-1); // 3
    const D = mod((c - d * r) * mod(r + d)); // 4
    let { isValid: Ns_D_is_sq, value: s } = uvRatio(Ns, D); // 5
    let s_ = mod(s * r0); // 6
    if (!(0, modular_js_1.isNegativeLE)(s_, P))
        s_ = mod(-s_);
    if (!Ns_D_is_sq)
        s = s_; // 7
    if (!Ns_D_is_sq)
        c = r; // 8
    const Nt = mod(c * (r - _1n) * D_MINUS_ONE_SQ - D); // 9
    const s2 = s * s;
    const W0 = mod((s + s) * D); // 10
    const W1 = mod(Nt * SQRT_AD_MINUS_ONE); // 11
    const W2 = mod(_1n - s2); // 12
    const W3 = mod(_1n + s2); // 13
    return new exports.ed25519.ExtendedPoint(mod(W0 * W3), mod(W2 * W1), mod(W1 * W3), mod(W0 * W2));
}
/**
 * Each ed25519/ExtendedPoint has 8 different equivalent points. This can be
 * a source of bugs for protocols like ring signatures. Ristretto was created to solve this.
 * Ristretto point operates in X:Y:Z:T extended coordinates like ExtendedPoint,
 * but it should work in its own namespace: do not combine those two.
 * https://datatracker.ietf.org/doc/html/draft-irtf-cfrg-ristretto255-decaf448
 */
class RistPoint {
    // Private property to discourage combining ExtendedPoint + RistrettoPoint
    // Always use Ristretto encoding/decoding instead.
    constructor(ep) {
        this.ep = ep;
    }
    static fromAffine(ap) {
        return new RistPoint(exports.ed25519.ExtendedPoint.fromAffine(ap));
    }
    /**
     * Takes uniform output of 64-byte hash function like sha512 and converts it to `RistrettoPoint`.
     * The hash-to-group operation applies Elligator twice and adds the results.
     * **Note:** this is one-way map, there is no conversion from point to hash.
     * https://ristretto.group/formulas/elligator.html
     * @param hex 64-byte output of a hash function
     */
    static hashToCurve(hex) {
        hex = (0, utils_js_1.ensureBytes)('ristrettoHash', hex, 64);
        const r1 = bytes255ToNumberLE(hex.slice(0, 32));
        const R1 = calcElligatorRistrettoMap(r1);
        const r2 = bytes255ToNumberLE(hex.slice(32, 64));
        const R2 = calcElligatorRistrettoMap(r2);
        return new RistPoint(R1.add(R2));
    }
    /**
     * Converts ristretto-encoded string to ristretto point.
     * https://ristretto.group/formulas/decoding.html
     * @param hex Ristretto-encoded 32 bytes. Not every 32-byte string is valid ristretto encoding
     */
    static fromHex(hex) {
        hex = (0, utils_js_1.ensureBytes)('ristrettoHex', hex, 32);
        const { a, d } = exports.ed25519.CURVE;
        const P = exports.ed25519.CURVE.Fp.ORDER;
        const mod = exports.ed25519.CURVE.Fp.create;
        const emsg = 'RistrettoPoint.fromHex: the hex is not valid encoding of RistrettoPoint';
        const s = bytes255ToNumberLE(hex);
        // 1. Check that s_bytes is the canonical encoding of a field element, or else abort.
        // 3. Check that s is non-negative, or else abort
        if (!(0, utils_js_1.equalBytes)((0, utils_js_1.numberToBytesLE)(s, 32), hex) || (0, modular_js_1.isNegativeLE)(s, P))
            throw new Error(emsg);
        const s2 = mod(s * s);
        const u1 = mod(_1n + a * s2); // 4 (a is -1)
        const u2 = mod(_1n - a * s2); // 5
        const u1_2 = mod(u1 * u1);
        const u2_2 = mod(u2 * u2);
        const v = mod(a * d * u1_2 - u2_2); // 6
        const { isValid, value: I } = invertSqrt(mod(v * u2_2)); // 7
        const Dx = mod(I * u2); // 8
        const Dy = mod(I * Dx * v); // 9
        let x = mod((s + s) * Dx); // 10
        if ((0, modular_js_1.isNegativeLE)(x, P))
            x = mod(-x); // 10
        const y = mod(u1 * Dy); // 11
        const t = mod(x * y); // 12
        if (!isValid || (0, modular_js_1.isNegativeLE)(t, P) || y === _0n)
            throw new Error(emsg);
        return new RistPoint(new exports.ed25519.ExtendedPoint(x, y, _1n, t));
    }
    /**
     * Encodes ristretto point to Uint8Array.
     * https://ristretto.group/formulas/encoding.html
     */
    toRawBytes() {
        let { ex: x, ey: y, ez: z, et: t } = this.ep;
        const P = exports.ed25519.CURVE.Fp.ORDER;
        const mod = exports.ed25519.CURVE.Fp.create;
        const u1 = mod(mod(z + y) * mod(z - y)); // 1
        const u2 = mod(x * y); // 2
        // Square root always exists
        const u2sq = mod(u2 * u2);
        const { value: invsqrt } = invertSqrt(mod(u1 * u2sq)); // 3
        const D1 = mod(invsqrt * u1); // 4
        const D2 = mod(invsqrt * u2); // 5
        const zInv = mod(D1 * D2 * t); // 6
        let D; // 7
        if ((0, modular_js_1.isNegativeLE)(t * zInv, P)) {
            let _x = mod(y * SQRT_M1);
            let _y = mod(x * SQRT_M1);
            x = _x;
            y = _y;
            D = mod(D1 * INVSQRT_A_MINUS_D);
        }
        else {
            D = D2; // 8
        }
        if ((0, modular_js_1.isNegativeLE)(x * zInv, P))
            y = mod(-y); // 9
        let s = mod((z - y) * D); // 10 (check footer's note, no sqrt(-a))
        if ((0, modular_js_1.isNegativeLE)(s, P))
            s = mod(-s);
        return (0, utils_js_1.numberToBytesLE)(s, 32); // 11
    }
    toHex() {
        return (0, utils_js_1.bytesToHex)(this.toRawBytes());
    }
    toString() {
        return this.toHex();
    }
    // Compare one point to another.
    equals(other) {
        assertRstPoint(other);
        const { ex: X1, ey: Y1 } = this.ep;
        const { ex: X2, ey: Y2 } = other.ep;
        const mod = exports.ed25519.CURVE.Fp.create;
        // (x1 * y2 == y1 * x2) | (y1 * y2 == x1 * x2)
        const one = mod(X1 * Y2) === mod(Y1 * X2);
        const two = mod(Y1 * Y2) === mod(X1 * X2);
        return one || two;
    }
    add(other) {
        assertRstPoint(other);
        return new RistPoint(this.ep.add(other.ep));
    }
    subtract(other) {
        assertRstPoint(other);
        return new RistPoint(this.ep.subtract(other.ep));
    }
    multiply(scalar) {
        return new RistPoint(this.ep.multiply(scalar));
    }
    multiplyUnsafe(scalar) {
        return new RistPoint(this.ep.multiplyUnsafe(scalar));
    }
    double() {
        return new RistPoint(this.ep.double());
    }
    negate() {
        return new RistPoint(this.ep.negate());
    }
}
exports.RistrettoPoint = (() => {
    if (!RistPoint.BASE)
        RistPoint.BASE = new RistPoint(exports.ed25519.ExtendedPoint.BASE);
    if (!RistPoint.ZERO)
        RistPoint.ZERO = new RistPoint(exports.ed25519.ExtendedPoint.ZERO);
    return RistPoint;
})();
// Hashing to ristretto255. https://www.rfc-editor.org/rfc/rfc9380#appendix-B
const hashToRistretto255 = (msg, options) => {
    const d = options.DST;
    const DST = typeof d === 'string' ? (0, utils_1.utf8ToBytes)(d) : d;
    const uniform_bytes = (0, hash_to_curve_js_1.expand_message_xmd)(msg, DST, 64, sha512_1.sha512);
    const P = RistPoint.hashToCurve(uniform_bytes);
    return P;
};
exports.hashToRistretto255 = hashToRistretto255;
exports.hash_to_ristretto255 = exports.hashToRistretto255; // legacy


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_assert.js":
/*!******************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/_assert.js ***!
  \******************************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.anumber = anumber;
exports.number = anumber;
exports.abytes = abytes;
exports.bytes = abytes;
exports.ahash = ahash;
exports.aexists = aexists;
exports.aoutput = aoutput;
function anumber(n) {
    if (!Number.isSafeInteger(n) || n < 0)
        throw new Error('positive integer expected, got ' + n);
}
// copied from utils
function isBytes(a) {
    return a instanceof Uint8Array || (ArrayBuffer.isView(a) && a.constructor.name === 'Uint8Array');
}
function abytes(b, ...lengths) {
    if (!isBytes(b))
        throw new Error('Uint8Array expected');
    if (lengths.length > 0 && !lengths.includes(b.length))
        throw new Error('Uint8Array expected of length ' + lengths + ', got length=' + b.length);
}
function ahash(h) {
    if (typeof h !== 'function' || typeof h.create !== 'function')
        throw new Error('Hash should be wrapped by utils.wrapConstructor');
    anumber(h.outputLen);
    anumber(h.blockLen);
}
function aexists(instance, checkFinished = true) {
    if (instance.destroyed)
        throw new Error('Hash instance has been destroyed');
    if (checkFinished && instance.finished)
        throw new Error('Hash#digest() has already been called');
}
function aoutput(out, instance) {
    abytes(out);
    const min = instance.outputLen;
    if (out.length < min) {
        throw new Error('digestInto() expects output buffer of length at least ' + min);
    }
}
const assert = {
    number: anumber,
    bytes: abytes,
    hash: ahash,
    exists: aexists,
    output: aoutput,
};
exports["default"] = assert;


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_md.js":
/*!**************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/_md.js ***!
  \**************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.HashMD = exports.Maj = exports.Chi = void 0;
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_assert.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
/**
 * Polyfill for Safari 14
 */
function setBigUint64(view, byteOffset, value, isLE) {
    if (typeof view.setBigUint64 === 'function')
        return view.setBigUint64(byteOffset, value, isLE);
    const _32n = BigInt(32);
    const _u32_max = BigInt(0xffffffff);
    const wh = Number((value >> _32n) & _u32_max);
    const wl = Number(value & _u32_max);
    const h = isLE ? 4 : 0;
    const l = isLE ? 0 : 4;
    view.setUint32(byteOffset + h, wh, isLE);
    view.setUint32(byteOffset + l, wl, isLE);
}
/**
 * Choice: a ? b : c
 */
const Chi = (a, b, c) => (a & b) ^ (~a & c);
exports.Chi = Chi;
/**
 * Majority function, true if any two inputs is true
 */
const Maj = (a, b, c) => (a & b) ^ (a & c) ^ (b & c);
exports.Maj = Maj;
/**
 * Merkle-Damgard hash construction base class.
 * Could be used to create MD5, RIPEMD, SHA1, SHA2.
 */
class HashMD extends utils_js_1.Hash {
    constructor(blockLen, outputLen, padOffset, isLE) {
        super();
        this.blockLen = blockLen;
        this.outputLen = outputLen;
        this.padOffset = padOffset;
        this.isLE = isLE;
        this.finished = false;
        this.length = 0;
        this.pos = 0;
        this.destroyed = false;
        this.buffer = new Uint8Array(blockLen);
        this.view = (0, utils_js_1.createView)(this.buffer);
    }
    update(data) {
        (0, _assert_js_1.aexists)(this);
        const { view, buffer, blockLen } = this;
        data = (0, utils_js_1.toBytes)(data);
        const len = data.length;
        for (let pos = 0; pos < len;) {
            const take = Math.min(blockLen - this.pos, len - pos);
            // Fast path: we have at least one block in input, cast it to view and process
            if (take === blockLen) {
                const dataView = (0, utils_js_1.createView)(data);
                for (; blockLen <= len - pos; pos += blockLen)
                    this.process(dataView, pos);
                continue;
            }
            buffer.set(data.subarray(pos, pos + take), this.pos);
            this.pos += take;
            pos += take;
            if (this.pos === blockLen) {
                this.process(view, 0);
                this.pos = 0;
            }
        }
        this.length += data.length;
        this.roundClean();
        return this;
    }
    digestInto(out) {
        (0, _assert_js_1.aexists)(this);
        (0, _assert_js_1.aoutput)(out, this);
        this.finished = true;
        // Padding
        // We can avoid allocation of buffer for padding completely if it
        // was previously not allocated here. But it won't change performance.
        const { buffer, view, blockLen, isLE } = this;
        let { pos } = this;
        // append the bit '1' to the message
        buffer[pos++] = 0b10000000;
        this.buffer.subarray(pos).fill(0);
        // we have less than padOffset left in buffer, so we cannot put length in
        // current block, need process it and pad again
        if (this.padOffset > blockLen - pos) {
            this.process(view, 0);
            pos = 0;
        }
        // Pad until full block byte with zeros
        for (let i = pos; i < blockLen; i++)
            buffer[i] = 0;
        // Note: sha512 requires length to be 128bit integer, but length in JS will overflow before that
        // You need to write around 2 exabytes (u64_max / 8 / (1024**6)) for this to happen.
        // So we just write lowest 64 bits of that value.
        setBigUint64(view, blockLen - 8, BigInt(this.length * 8), isLE);
        this.process(view, 0);
        const oview = (0, utils_js_1.createView)(out);
        const len = this.outputLen;
        // NOTE: we do division by 4 later, which should be fused in single op with modulo by JIT
        if (len % 4)
            throw new Error('_sha2: outputLen should be aligned to 32bit');
        const outLen = len / 4;
        const state = this.get();
        if (outLen > state.length)
            throw new Error('_sha2: outputLen bigger than state');
        for (let i = 0; i < outLen; i++)
            oview.setUint32(4 * i, state[i], isLE);
    }
    digest() {
        const { buffer, outputLen } = this;
        this.digestInto(buffer);
        const res = buffer.slice(0, outputLen);
        this.destroy();
        return res;
    }
    _cloneInto(to) {
        to || (to = new this.constructor());
        to.set(...this.get());
        const { blockLen, buffer, length, finished, destroyed, pos } = this;
        to.length = length;
        to.pos = pos;
        to.finished = finished;
        to.destroyed = destroyed;
        if (length % blockLen)
            to.buffer.set(buffer);
        return to;
    }
}
exports.HashMD = HashMD;


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_u64.js":
/*!***************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/_u64.js ***!
  \***************************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.add5L = exports.add5H = exports.add4H = exports.add4L = exports.add3H = exports.add3L = exports.rotlBL = exports.rotlBH = exports.rotlSL = exports.rotlSH = exports.rotr32L = exports.rotr32H = exports.rotrBL = exports.rotrBH = exports.rotrSL = exports.rotrSH = exports.shrSL = exports.shrSH = exports.toBig = void 0;
exports.fromBig = fromBig;
exports.split = split;
exports.add = add;
const U32_MASK64 = /* @__PURE__ */ BigInt(2 ** 32 - 1);
const _32n = /* @__PURE__ */ BigInt(32);
// BigUint64Array is too slow as per 2024, so we implement it using Uint32Array.
// TODO: re-check https://issues.chromium.org/issues/42212588
function fromBig(n, le = false) {
    if (le)
        return { h: Number(n & U32_MASK64), l: Number((n >> _32n) & U32_MASK64) };
    return { h: Number((n >> _32n) & U32_MASK64) | 0, l: Number(n & U32_MASK64) | 0 };
}
function split(lst, le = false) {
    let Ah = new Uint32Array(lst.length);
    let Al = new Uint32Array(lst.length);
    for (let i = 0; i < lst.length; i++) {
        const { h, l } = fromBig(lst[i], le);
        [Ah[i], Al[i]] = [h, l];
    }
    return [Ah, Al];
}
const toBig = (h, l) => (BigInt(h >>> 0) << _32n) | BigInt(l >>> 0);
exports.toBig = toBig;
// for Shift in [0, 32)
const shrSH = (h, _l, s) => h >>> s;
exports.shrSH = shrSH;
const shrSL = (h, l, s) => (h << (32 - s)) | (l >>> s);
exports.shrSL = shrSL;
// Right rotate for Shift in [1, 32)
const rotrSH = (h, l, s) => (h >>> s) | (l << (32 - s));
exports.rotrSH = rotrSH;
const rotrSL = (h, l, s) => (h << (32 - s)) | (l >>> s);
exports.rotrSL = rotrSL;
// Right rotate for Shift in (32, 64), NOTE: 32 is special case.
const rotrBH = (h, l, s) => (h << (64 - s)) | (l >>> (s - 32));
exports.rotrBH = rotrBH;
const rotrBL = (h, l, s) => (h >>> (s - 32)) | (l << (64 - s));
exports.rotrBL = rotrBL;
// Right rotate for shift===32 (just swaps l&h)
const rotr32H = (_h, l) => l;
exports.rotr32H = rotr32H;
const rotr32L = (h, _l) => h;
exports.rotr32L = rotr32L;
// Left rotate for Shift in [1, 32)
const rotlSH = (h, l, s) => (h << s) | (l >>> (32 - s));
exports.rotlSH = rotlSH;
const rotlSL = (h, l, s) => (l << s) | (h >>> (32 - s));
exports.rotlSL = rotlSL;
// Left rotate for Shift in (32, 64), NOTE: 32 is special case.
const rotlBH = (h, l, s) => (l << (s - 32)) | (h >>> (64 - s));
exports.rotlBH = rotlBH;
const rotlBL = (h, l, s) => (h << (s - 32)) | (l >>> (64 - s));
exports.rotlBL = rotlBL;
// JS uses 32-bit signed integers for bitwise operations which means we cannot
// simple take carry out of low bit sum by shift, we need to use division.
function add(Ah, Al, Bh, Bl) {
    const l = (Al >>> 0) + (Bl >>> 0);
    return { h: (Ah + Bh + ((l / 2 ** 32) | 0)) | 0, l: l | 0 };
}
// Addition with more than 2 elements
const add3L = (Al, Bl, Cl) => (Al >>> 0) + (Bl >>> 0) + (Cl >>> 0);
exports.add3L = add3L;
const add3H = (low, Ah, Bh, Ch) => (Ah + Bh + Ch + ((low / 2 ** 32) | 0)) | 0;
exports.add3H = add3H;
const add4L = (Al, Bl, Cl, Dl) => (Al >>> 0) + (Bl >>> 0) + (Cl >>> 0) + (Dl >>> 0);
exports.add4L = add4L;
const add4H = (low, Ah, Bh, Ch, Dh) => (Ah + Bh + Ch + Dh + ((low / 2 ** 32) | 0)) | 0;
exports.add4H = add4H;
const add5L = (Al, Bl, Cl, Dl, El) => (Al >>> 0) + (Bl >>> 0) + (Cl >>> 0) + (Dl >>> 0) + (El >>> 0);
exports.add5L = add5L;
const add5H = (low, Ah, Bh, Ch, Dh, Eh) => (Ah + Bh + Ch + Dh + Eh + ((low / 2 ** 32) | 0)) | 0;
exports.add5H = add5H;
// prettier-ignore
const u64 = {
    fromBig, split, toBig,
    shrSH, shrSL,
    rotrSH, rotrSL, rotrBH, rotrBL,
    rotr32H, rotr32L,
    rotlSH, rotlSL, rotlBH, rotlBL,
    add, add3L, add3H, add4L, add4H, add5H, add5L,
};
exports["default"] = u64;


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/crypto.js":
/*!*****************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/crypto.js ***!
  \*****************************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.crypto = void 0;
exports.crypto = typeof globalThis === 'object' && 'crypto' in globalThis ? globalThis.crypto : undefined;


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/hmac.js":
/*!***************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/hmac.js ***!
  \***************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.hmac = exports.HMAC = void 0;
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_assert.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
// HMAC (RFC 2104)
class HMAC extends utils_js_1.Hash {
    constructor(hash, _key) {
        super();
        this.finished = false;
        this.destroyed = false;
        (0, _assert_js_1.ahash)(hash);
        const key = (0, utils_js_1.toBytes)(_key);
        this.iHash = hash.create();
        if (typeof this.iHash.update !== 'function')
            throw new Error('Expected instance of class which extends utils.Hash');
        this.blockLen = this.iHash.blockLen;
        this.outputLen = this.iHash.outputLen;
        const blockLen = this.blockLen;
        const pad = new Uint8Array(blockLen);
        // blockLen can be bigger than outputLen
        pad.set(key.length > blockLen ? hash.create().update(key).digest() : key);
        for (let i = 0; i < pad.length; i++)
            pad[i] ^= 0x36;
        this.iHash.update(pad);
        // By doing update (processing of first block) of outer hash here we can re-use it between multiple calls via clone
        this.oHash = hash.create();
        // Undo internal XOR && apply outer XOR
        for (let i = 0; i < pad.length; i++)
            pad[i] ^= 0x36 ^ 0x5c;
        this.oHash.update(pad);
        pad.fill(0);
    }
    update(buf) {
        (0, _assert_js_1.aexists)(this);
        this.iHash.update(buf);
        return this;
    }
    digestInto(out) {
        (0, _assert_js_1.aexists)(this);
        (0, _assert_js_1.abytes)(out, this.outputLen);
        this.finished = true;
        this.iHash.digestInto(out);
        this.oHash.update(out);
        this.oHash.digestInto(out);
        this.destroy();
    }
    digest() {
        const out = new Uint8Array(this.oHash.outputLen);
        this.digestInto(out);
        return out;
    }
    _cloneInto(to) {
        // Create new instance without calling constructor since key already in state and we don't know it.
        to || (to = Object.create(Object.getPrototypeOf(this), {}));
        const { oHash, iHash, finished, destroyed, blockLen, outputLen } = this;
        to = to;
        to.finished = finished;
        to.destroyed = destroyed;
        to.blockLen = blockLen;
        to.outputLen = outputLen;
        to.oHash = oHash._cloneInto(to.oHash);
        to.iHash = iHash._cloneInto(to.iHash);
        return to;
    }
    destroy() {
        this.destroyed = true;
        this.oHash.destroy();
        this.iHash.destroy();
    }
}
exports.HMAC = HMAC;
/**
 * HMAC: RFC2104 message authentication code.
 * @param hash - function that would be used e.g. sha256
 * @param key - message key
 * @param message - message data
 * @example
 * import { hmac } from '@noble/hashes/hmac';
 * import { sha256 } from '@noble/hashes/sha2';
 * const mac1 = hmac(sha256, 'key', 'message');
 */
const hmac = (hash, key, message) => new HMAC(hash, key).update(message).digest();
exports.hmac = hmac;
exports.hmac.create = (hash, key) => new HMAC(hash, key);


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/sha256.js":
/*!*****************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/sha256.js ***!
  \*****************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sha224 = exports.sha256 = exports.SHA256 = void 0;
const _md_js_1 = __webpack_require__(/*! ./_md.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_md.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
// SHA2-256 need to try 2^128 hashes to execute birthday attack.
// BTC network is doing 2^70 hashes/sec (2^95 hashes/year) as per late 2024.
// Round constants:
// first 32 bits of the fractional parts of the cube roots of the first 64 primes 2..311)
// prettier-ignore
const SHA256_K = /* @__PURE__ */ new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
]);
// Initial state:
// first 32 bits of the fractional parts of the square roots of the first 8 primes 2..19
// prettier-ignore
const SHA256_IV = /* @__PURE__ */ new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
]);
// Temporary buffer, not used to store anything between runs
// Named this way because it matches specification.
const SHA256_W = /* @__PURE__ */ new Uint32Array(64);
class SHA256 extends _md_js_1.HashMD {
    constructor() {
        super(64, 32, 8, false);
        // We cannot use array here since array allows indexing by variable
        // which means optimizer/compiler cannot use registers.
        this.A = SHA256_IV[0] | 0;
        this.B = SHA256_IV[1] | 0;
        this.C = SHA256_IV[2] | 0;
        this.D = SHA256_IV[3] | 0;
        this.E = SHA256_IV[4] | 0;
        this.F = SHA256_IV[5] | 0;
        this.G = SHA256_IV[6] | 0;
        this.H = SHA256_IV[7] | 0;
    }
    get() {
        const { A, B, C, D, E, F, G, H } = this;
        return [A, B, C, D, E, F, G, H];
    }
    // prettier-ignore
    set(A, B, C, D, E, F, G, H) {
        this.A = A | 0;
        this.B = B | 0;
        this.C = C | 0;
        this.D = D | 0;
        this.E = E | 0;
        this.F = F | 0;
        this.G = G | 0;
        this.H = H | 0;
    }
    process(view, offset) {
        // Extend the first 16 words into the remaining 48 words w[16..63] of the message schedule array
        for (let i = 0; i < 16; i++, offset += 4)
            SHA256_W[i] = view.getUint32(offset, false);
        for (let i = 16; i < 64; i++) {
            const W15 = SHA256_W[i - 15];
            const W2 = SHA256_W[i - 2];
            const s0 = (0, utils_js_1.rotr)(W15, 7) ^ (0, utils_js_1.rotr)(W15, 18) ^ (W15 >>> 3);
            const s1 = (0, utils_js_1.rotr)(W2, 17) ^ (0, utils_js_1.rotr)(W2, 19) ^ (W2 >>> 10);
            SHA256_W[i] = (s1 + SHA256_W[i - 7] + s0 + SHA256_W[i - 16]) | 0;
        }
        // Compression function main loop, 64 rounds
        let { A, B, C, D, E, F, G, H } = this;
        for (let i = 0; i < 64; i++) {
            const sigma1 = (0, utils_js_1.rotr)(E, 6) ^ (0, utils_js_1.rotr)(E, 11) ^ (0, utils_js_1.rotr)(E, 25);
            const T1 = (H + sigma1 + (0, _md_js_1.Chi)(E, F, G) + SHA256_K[i] + SHA256_W[i]) | 0;
            const sigma0 = (0, utils_js_1.rotr)(A, 2) ^ (0, utils_js_1.rotr)(A, 13) ^ (0, utils_js_1.rotr)(A, 22);
            const T2 = (sigma0 + (0, _md_js_1.Maj)(A, B, C)) | 0;
            H = G;
            G = F;
            F = E;
            E = (D + T1) | 0;
            D = C;
            C = B;
            B = A;
            A = (T1 + T2) | 0;
        }
        // Add the compressed chunk to the current hash value
        A = (A + this.A) | 0;
        B = (B + this.B) | 0;
        C = (C + this.C) | 0;
        D = (D + this.D) | 0;
        E = (E + this.E) | 0;
        F = (F + this.F) | 0;
        G = (G + this.G) | 0;
        H = (H + this.H) | 0;
        this.set(A, B, C, D, E, F, G, H);
    }
    roundClean() {
        SHA256_W.fill(0);
    }
    destroy() {
        this.set(0, 0, 0, 0, 0, 0, 0, 0);
        this.buffer.fill(0);
    }
}
exports.SHA256 = SHA256;
// Constants from https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf
class SHA224 extends SHA256 {
    constructor() {
        super();
        this.A = 0xc1059ed8 | 0;
        this.B = 0x367cd507 | 0;
        this.C = 0x3070dd17 | 0;
        this.D = 0xf70e5939 | 0;
        this.E = 0xffc00b31 | 0;
        this.F = 0x68581511 | 0;
        this.G = 0x64f98fa7 | 0;
        this.H = 0xbefa4fa4 | 0;
        this.outputLen = 28;
    }
}
/**
 * SHA2-256 hash function
 * @param message - data that would be hashed
 */
exports.sha256 = (0, utils_js_1.wrapConstructor)(() => new SHA256());
/**
 * SHA2-224 hash function
 */
exports.sha224 = (0, utils_js_1.wrapConstructor)(() => new SHA224());


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/sha512.js":
/*!*****************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/sha512.js ***!
  \*****************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sha384 = exports.sha512_256 = exports.sha512_224 = exports.sha512 = exports.SHA384 = exports.SHA512_256 = exports.SHA512_224 = exports.SHA512 = void 0;
const _md_js_1 = __webpack_require__(/*! ./_md.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_md.js");
const _u64_js_1 = __webpack_require__(/*! ./_u64.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_u64.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
// Round contants (first 32 bits of the fractional parts of the cube roots of the first 80 primes 2..409):
// prettier-ignore
const [SHA512_Kh, SHA512_Kl] = /* @__PURE__ */ (() => _u64_js_1.default.split([
    '0x428a2f98d728ae22', '0x7137449123ef65cd', '0xb5c0fbcfec4d3b2f', '0xe9b5dba58189dbbc',
    '0x3956c25bf348b538', '0x59f111f1b605d019', '0x923f82a4af194f9b', '0xab1c5ed5da6d8118',
    '0xd807aa98a3030242', '0x12835b0145706fbe', '0x243185be4ee4b28c', '0x550c7dc3d5ffb4e2',
    '0x72be5d74f27b896f', '0x80deb1fe3b1696b1', '0x9bdc06a725c71235', '0xc19bf174cf692694',
    '0xe49b69c19ef14ad2', '0xefbe4786384f25e3', '0x0fc19dc68b8cd5b5', '0x240ca1cc77ac9c65',
    '0x2de92c6f592b0275', '0x4a7484aa6ea6e483', '0x5cb0a9dcbd41fbd4', '0x76f988da831153b5',
    '0x983e5152ee66dfab', '0xa831c66d2db43210', '0xb00327c898fb213f', '0xbf597fc7beef0ee4',
    '0xc6e00bf33da88fc2', '0xd5a79147930aa725', '0x06ca6351e003826f', '0x142929670a0e6e70',
    '0x27b70a8546d22ffc', '0x2e1b21385c26c926', '0x4d2c6dfc5ac42aed', '0x53380d139d95b3df',
    '0x650a73548baf63de', '0x766a0abb3c77b2a8', '0x81c2c92e47edaee6', '0x92722c851482353b',
    '0xa2bfe8a14cf10364', '0xa81a664bbc423001', '0xc24b8b70d0f89791', '0xc76c51a30654be30',
    '0xd192e819d6ef5218', '0xd69906245565a910', '0xf40e35855771202a', '0x106aa07032bbd1b8',
    '0x19a4c116b8d2d0c8', '0x1e376c085141ab53', '0x2748774cdf8eeb99', '0x34b0bcb5e19b48a8',
    '0x391c0cb3c5c95a63', '0x4ed8aa4ae3418acb', '0x5b9cca4f7763e373', '0x682e6ff3d6b2b8a3',
    '0x748f82ee5defb2fc', '0x78a5636f43172f60', '0x84c87814a1f0ab72', '0x8cc702081a6439ec',
    '0x90befffa23631e28', '0xa4506cebde82bde9', '0xbef9a3f7b2c67915', '0xc67178f2e372532b',
    '0xca273eceea26619c', '0xd186b8c721c0c207', '0xeada7dd6cde0eb1e', '0xf57d4f7fee6ed178',
    '0x06f067aa72176fba', '0x0a637dc5a2c898a6', '0x113f9804bef90dae', '0x1b710b35131c471b',
    '0x28db77f523047d84', '0x32caab7b40c72493', '0x3c9ebe0a15c9bebc', '0x431d67c49c100d4c',
    '0x4cc5d4becb3e42b6', '0x597f299cfc657e2a', '0x5fcb6fab3ad6faec', '0x6c44198c4a475817'
].map(n => BigInt(n))))();
// Temporary buffer, not used to store anything between runs
const SHA512_W_H = /* @__PURE__ */ new Uint32Array(80);
const SHA512_W_L = /* @__PURE__ */ new Uint32Array(80);
class SHA512 extends _md_js_1.HashMD {
    constructor() {
        super(128, 64, 16, false);
        // We cannot use array here since array allows indexing by variable which means optimizer/compiler cannot use registers.
        // Also looks cleaner and easier to verify with spec.
        // Initial state (first 32 bits of the fractional parts of the square roots of the first 8 primes 2..19):
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0x6a09e667 | 0;
        this.Al = 0xf3bcc908 | 0;
        this.Bh = 0xbb67ae85 | 0;
        this.Bl = 0x84caa73b | 0;
        this.Ch = 0x3c6ef372 | 0;
        this.Cl = 0xfe94f82b | 0;
        this.Dh = 0xa54ff53a | 0;
        this.Dl = 0x5f1d36f1 | 0;
        this.Eh = 0x510e527f | 0;
        this.El = 0xade682d1 | 0;
        this.Fh = 0x9b05688c | 0;
        this.Fl = 0x2b3e6c1f | 0;
        this.Gh = 0x1f83d9ab | 0;
        this.Gl = 0xfb41bd6b | 0;
        this.Hh = 0x5be0cd19 | 0;
        this.Hl = 0x137e2179 | 0;
    }
    // prettier-ignore
    get() {
        const { Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl } = this;
        return [Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl];
    }
    // prettier-ignore
    set(Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl) {
        this.Ah = Ah | 0;
        this.Al = Al | 0;
        this.Bh = Bh | 0;
        this.Bl = Bl | 0;
        this.Ch = Ch | 0;
        this.Cl = Cl | 0;
        this.Dh = Dh | 0;
        this.Dl = Dl | 0;
        this.Eh = Eh | 0;
        this.El = El | 0;
        this.Fh = Fh | 0;
        this.Fl = Fl | 0;
        this.Gh = Gh | 0;
        this.Gl = Gl | 0;
        this.Hh = Hh | 0;
        this.Hl = Hl | 0;
    }
    process(view, offset) {
        // Extend the first 16 words into the remaining 64 words w[16..79] of the message schedule array
        for (let i = 0; i < 16; i++, offset += 4) {
            SHA512_W_H[i] = view.getUint32(offset);
            SHA512_W_L[i] = view.getUint32((offset += 4));
        }
        for (let i = 16; i < 80; i++) {
            // s0 := (w[i-15] rightrotate 1) xor (w[i-15] rightrotate 8) xor (w[i-15] rightshift 7)
            const W15h = SHA512_W_H[i - 15] | 0;
            const W15l = SHA512_W_L[i - 15] | 0;
            const s0h = _u64_js_1.default.rotrSH(W15h, W15l, 1) ^ _u64_js_1.default.rotrSH(W15h, W15l, 8) ^ _u64_js_1.default.shrSH(W15h, W15l, 7);
            const s0l = _u64_js_1.default.rotrSL(W15h, W15l, 1) ^ _u64_js_1.default.rotrSL(W15h, W15l, 8) ^ _u64_js_1.default.shrSL(W15h, W15l, 7);
            // s1 := (w[i-2] rightrotate 19) xor (w[i-2] rightrotate 61) xor (w[i-2] rightshift 6)
            const W2h = SHA512_W_H[i - 2] | 0;
            const W2l = SHA512_W_L[i - 2] | 0;
            const s1h = _u64_js_1.default.rotrSH(W2h, W2l, 19) ^ _u64_js_1.default.rotrBH(W2h, W2l, 61) ^ _u64_js_1.default.shrSH(W2h, W2l, 6);
            const s1l = _u64_js_1.default.rotrSL(W2h, W2l, 19) ^ _u64_js_1.default.rotrBL(W2h, W2l, 61) ^ _u64_js_1.default.shrSL(W2h, W2l, 6);
            // SHA256_W[i] = s0 + s1 + SHA256_W[i - 7] + SHA256_W[i - 16];
            const SUMl = _u64_js_1.default.add4L(s0l, s1l, SHA512_W_L[i - 7], SHA512_W_L[i - 16]);
            const SUMh = _u64_js_1.default.add4H(SUMl, s0h, s1h, SHA512_W_H[i - 7], SHA512_W_H[i - 16]);
            SHA512_W_H[i] = SUMh | 0;
            SHA512_W_L[i] = SUMl | 0;
        }
        let { Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl } = this;
        // Compression function main loop, 80 rounds
        for (let i = 0; i < 80; i++) {
            // S1 := (e rightrotate 14) xor (e rightrotate 18) xor (e rightrotate 41)
            const sigma1h = _u64_js_1.default.rotrSH(Eh, El, 14) ^ _u64_js_1.default.rotrSH(Eh, El, 18) ^ _u64_js_1.default.rotrBH(Eh, El, 41);
            const sigma1l = _u64_js_1.default.rotrSL(Eh, El, 14) ^ _u64_js_1.default.rotrSL(Eh, El, 18) ^ _u64_js_1.default.rotrBL(Eh, El, 41);
            //const T1 = (H + sigma1 + Chi(E, F, G) + SHA256_K[i] + SHA256_W[i]) | 0;
            const CHIh = (Eh & Fh) ^ (~Eh & Gh);
            const CHIl = (El & Fl) ^ (~El & Gl);
            // T1 = H + sigma1 + Chi(E, F, G) + SHA512_K[i] + SHA512_W[i]
            // prettier-ignore
            const T1ll = _u64_js_1.default.add5L(Hl, sigma1l, CHIl, SHA512_Kl[i], SHA512_W_L[i]);
            const T1h = _u64_js_1.default.add5H(T1ll, Hh, sigma1h, CHIh, SHA512_Kh[i], SHA512_W_H[i]);
            const T1l = T1ll | 0;
            // S0 := (a rightrotate 28) xor (a rightrotate 34) xor (a rightrotate 39)
            const sigma0h = _u64_js_1.default.rotrSH(Ah, Al, 28) ^ _u64_js_1.default.rotrBH(Ah, Al, 34) ^ _u64_js_1.default.rotrBH(Ah, Al, 39);
            const sigma0l = _u64_js_1.default.rotrSL(Ah, Al, 28) ^ _u64_js_1.default.rotrBL(Ah, Al, 34) ^ _u64_js_1.default.rotrBL(Ah, Al, 39);
            const MAJh = (Ah & Bh) ^ (Ah & Ch) ^ (Bh & Ch);
            const MAJl = (Al & Bl) ^ (Al & Cl) ^ (Bl & Cl);
            Hh = Gh | 0;
            Hl = Gl | 0;
            Gh = Fh | 0;
            Gl = Fl | 0;
            Fh = Eh | 0;
            Fl = El | 0;
            ({ h: Eh, l: El } = _u64_js_1.default.add(Dh | 0, Dl | 0, T1h | 0, T1l | 0));
            Dh = Ch | 0;
            Dl = Cl | 0;
            Ch = Bh | 0;
            Cl = Bl | 0;
            Bh = Ah | 0;
            Bl = Al | 0;
            const All = _u64_js_1.default.add3L(T1l, sigma0l, MAJl);
            Ah = _u64_js_1.default.add3H(All, T1h, sigma0h, MAJh);
            Al = All | 0;
        }
        // Add the compressed chunk to the current hash value
        ({ h: Ah, l: Al } = _u64_js_1.default.add(this.Ah | 0, this.Al | 0, Ah | 0, Al | 0));
        ({ h: Bh, l: Bl } = _u64_js_1.default.add(this.Bh | 0, this.Bl | 0, Bh | 0, Bl | 0));
        ({ h: Ch, l: Cl } = _u64_js_1.default.add(this.Ch | 0, this.Cl | 0, Ch | 0, Cl | 0));
        ({ h: Dh, l: Dl } = _u64_js_1.default.add(this.Dh | 0, this.Dl | 0, Dh | 0, Dl | 0));
        ({ h: Eh, l: El } = _u64_js_1.default.add(this.Eh | 0, this.El | 0, Eh | 0, El | 0));
        ({ h: Fh, l: Fl } = _u64_js_1.default.add(this.Fh | 0, this.Fl | 0, Fh | 0, Fl | 0));
        ({ h: Gh, l: Gl } = _u64_js_1.default.add(this.Gh | 0, this.Gl | 0, Gh | 0, Gl | 0));
        ({ h: Hh, l: Hl } = _u64_js_1.default.add(this.Hh | 0, this.Hl | 0, Hh | 0, Hl | 0));
        this.set(Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl);
    }
    roundClean() {
        SHA512_W_H.fill(0);
        SHA512_W_L.fill(0);
    }
    destroy() {
        this.buffer.fill(0);
        this.set(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    }
}
exports.SHA512 = SHA512;
class SHA512_224 extends SHA512 {
    constructor() {
        super();
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0x8c3d37c8 | 0;
        this.Al = 0x19544da2 | 0;
        this.Bh = 0x73e19966 | 0;
        this.Bl = 0x89dcd4d6 | 0;
        this.Ch = 0x1dfab7ae | 0;
        this.Cl = 0x32ff9c82 | 0;
        this.Dh = 0x679dd514 | 0;
        this.Dl = 0x582f9fcf | 0;
        this.Eh = 0x0f6d2b69 | 0;
        this.El = 0x7bd44da8 | 0;
        this.Fh = 0x77e36f73 | 0;
        this.Fl = 0x04c48942 | 0;
        this.Gh = 0x3f9d85a8 | 0;
        this.Gl = 0x6a1d36c8 | 0;
        this.Hh = 0x1112e6ad | 0;
        this.Hl = 0x91d692a1 | 0;
        this.outputLen = 28;
    }
}
exports.SHA512_224 = SHA512_224;
class SHA512_256 extends SHA512 {
    constructor() {
        super();
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0x22312194 | 0;
        this.Al = 0xfc2bf72c | 0;
        this.Bh = 0x9f555fa3 | 0;
        this.Bl = 0xc84c64c2 | 0;
        this.Ch = 0x2393b86b | 0;
        this.Cl = 0x6f53b151 | 0;
        this.Dh = 0x96387719 | 0;
        this.Dl = 0x5940eabd | 0;
        this.Eh = 0x96283ee2 | 0;
        this.El = 0xa88effe3 | 0;
        this.Fh = 0xbe5e1e25 | 0;
        this.Fl = 0x53863992 | 0;
        this.Gh = 0x2b0199fc | 0;
        this.Gl = 0x2c85b8aa | 0;
        this.Hh = 0x0eb72ddc | 0;
        this.Hl = 0x81c52ca2 | 0;
        this.outputLen = 32;
    }
}
exports.SHA512_256 = SHA512_256;
class SHA384 extends SHA512 {
    constructor() {
        super();
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0xcbbb9d5d | 0;
        this.Al = 0xc1059ed8 | 0;
        this.Bh = 0x629a292a | 0;
        this.Bl = 0x367cd507 | 0;
        this.Ch = 0x9159015a | 0;
        this.Cl = 0x3070dd17 | 0;
        this.Dh = 0x152fecd8 | 0;
        this.Dl = 0xf70e5939 | 0;
        this.Eh = 0x67332667 | 0;
        this.El = 0xffc00b31 | 0;
        this.Fh = 0x8eb44a87 | 0;
        this.Fl = 0x68581511 | 0;
        this.Gh = 0xdb0c2e0d | 0;
        this.Gl = 0x64f98fa7 | 0;
        this.Hh = 0x47b5481d | 0;
        this.Hl = 0xbefa4fa4 | 0;
        this.outputLen = 48;
    }
}
exports.SHA384 = SHA384;
exports.sha512 = (0, utils_js_1.wrapConstructor)(() => new SHA512());
exports.sha512_224 = (0, utils_js_1.wrapConstructor)(() => new SHA512_224());
exports.sha512_256 = (0, utils_js_1.wrapConstructor)(() => new SHA512_256());
exports.sha384 = (0, utils_js_1.wrapConstructor)(() => new SHA384());


/***/ }),

/***/ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js":
/*!****************************************************************************!*\
  !*** ../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js ***!
  \****************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

/*! noble-hashes - MIT License (c) 2022 Paul Miller (paulmillr.com) */
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash = exports.nextTick = exports.byteSwapIfBE = exports.byteSwap = exports.isLE = exports.rotl = exports.rotr = exports.createView = exports.u32 = exports.u8 = void 0;
exports.isBytes = isBytes;
exports.byteSwap32 = byteSwap32;
exports.bytesToHex = bytesToHex;
exports.hexToBytes = hexToBytes;
exports.asyncLoop = asyncLoop;
exports.utf8ToBytes = utf8ToBytes;
exports.toBytes = toBytes;
exports.concatBytes = concatBytes;
exports.checkOpts = checkOpts;
exports.wrapConstructor = wrapConstructor;
exports.wrapConstructorWithOpts = wrapConstructorWithOpts;
exports.wrapXOFConstructorWithOpts = wrapXOFConstructorWithOpts;
exports.randomBytes = randomBytes;
// We use WebCrypto aka globalThis.crypto, which exists in browsers and node.js 16+.
// node.js versions earlier than v19 don't declare it in global scope.
// For node.js, package.json#exports field mapping rewrites import
// from `crypto` to `cryptoNode`, which imports native module.
// Makes the utils un-importable in browsers without a bundler.
// Once node.js 18 is deprecated (2025-04-30), we can just drop the import.
const crypto_1 = __webpack_require__(/*! @noble/hashes/crypto */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/crypto.js");
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/_assert.js");
// export { isBytes } from './_assert.js';
// We can't reuse isBytes from _assert, because somehow this causes huge perf issues
function isBytes(a) {
    return a instanceof Uint8Array || (ArrayBuffer.isView(a) && a.constructor.name === 'Uint8Array');
}
// Cast array to different type
const u8 = (arr) => new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
exports.u8 = u8;
const u32 = (arr) => new Uint32Array(arr.buffer, arr.byteOffset, Math.floor(arr.byteLength / 4));
exports.u32 = u32;
// Cast array to view
const createView = (arr) => new DataView(arr.buffer, arr.byteOffset, arr.byteLength);
exports.createView = createView;
// The rotate right (circular right shift) operation for uint32
const rotr = (word, shift) => (word << (32 - shift)) | (word >>> shift);
exports.rotr = rotr;
// The rotate left (circular left shift) operation for uint32
const rotl = (word, shift) => (word << shift) | ((word >>> (32 - shift)) >>> 0);
exports.rotl = rotl;
exports.isLE = (() => new Uint8Array(new Uint32Array([0x11223344]).buffer)[0] === 0x44)();
// The byte swap operation for uint32
const byteSwap = (word) => ((word << 24) & 0xff000000) |
    ((word << 8) & 0xff0000) |
    ((word >>> 8) & 0xff00) |
    ((word >>> 24) & 0xff);
exports.byteSwap = byteSwap;
// Conditionally byte swap if on a big-endian platform
exports.byteSwapIfBE = exports.isLE ? (n) => n : (n) => (0, exports.byteSwap)(n);
// In place byte swap for Uint32Array
function byteSwap32(arr) {
    for (let i = 0; i < arr.length; i++) {
        arr[i] = (0, exports.byteSwap)(arr[i]);
    }
}
// Array where index 0xf0 (240) is mapped to string 'f0'
const hexes = /* @__PURE__ */ Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));
/**
 * @example bytesToHex(Uint8Array.from([0xca, 0xfe, 0x01, 0x23])) // 'cafe0123'
 */
function bytesToHex(bytes) {
    (0, _assert_js_1.abytes)(bytes);
    // pre-caching improves the speed 6x
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
        hex += hexes[bytes[i]];
    }
    return hex;
}
// We use optimized technique to convert hex string to byte array
const asciis = { _0: 48, _9: 57, A: 65, F: 70, a: 97, f: 102 };
function asciiToBase16(ch) {
    if (ch >= asciis._0 && ch <= asciis._9)
        return ch - asciis._0; // '2' => 50-48
    if (ch >= asciis.A && ch <= asciis.F)
        return ch - (asciis.A - 10); // 'B' => 66-(65-10)
    if (ch >= asciis.a && ch <= asciis.f)
        return ch - (asciis.a - 10); // 'b' => 98-(97-10)
    return;
}
/**
 * @example hexToBytes('cafe0123') // Uint8Array.from([0xca, 0xfe, 0x01, 0x23])
 */
function hexToBytes(hex) {
    if (typeof hex !== 'string')
        throw new Error('hex string expected, got ' + typeof hex);
    const hl = hex.length;
    const al = hl / 2;
    if (hl % 2)
        throw new Error('padded hex string expected, got unpadded hex of length ' + hl);
    const array = new Uint8Array(al);
    for (let ai = 0, hi = 0; ai < al; ai++, hi += 2) {
        const n1 = asciiToBase16(hex.charCodeAt(hi));
        const n2 = asciiToBase16(hex.charCodeAt(hi + 1));
        if (n1 === undefined || n2 === undefined) {
            const char = hex[hi] + hex[hi + 1];
            throw new Error('hex string expected, got non-hex character "' + char + '" at index ' + hi);
        }
        array[ai] = n1 * 16 + n2; // multiply first octet, e.g. 'a3' => 10*16+3 => 160 + 3 => 163
    }
    return array;
}
// There is no setImmediate in browser and setTimeout is slow.
// call of async fn will return Promise, which will be fullfiled only on
// next scheduler queue processing step and this is exactly what we need.
const nextTick = async () => { };
exports.nextTick = nextTick;
// Returns control to thread each 'tick' ms to avoid blocking
async function asyncLoop(iters, tick, cb) {
    let ts = Date.now();
    for (let i = 0; i < iters; i++) {
        cb(i);
        // Date.now() is not monotonic, so in case if clock goes backwards we return return control too
        const diff = Date.now() - ts;
        if (diff >= 0 && diff < tick)
            continue;
        await (0, exports.nextTick)();
        ts += diff;
    }
}
/**
 * @example utf8ToBytes('abc') // new Uint8Array([97, 98, 99])
 */
function utf8ToBytes(str) {
    if (typeof str !== 'string')
        throw new Error('utf8ToBytes expected string, got ' + typeof str);
    return new Uint8Array(new TextEncoder().encode(str)); // https://bugzil.la/1681809
}
/**
 * Normalizes (non-hex) string or Uint8Array to Uint8Array.
 * Warning: when Uint8Array is passed, it would NOT get copied.
 * Keep in mind for future mutable operations.
 */
function toBytes(data) {
    if (typeof data === 'string')
        data = utf8ToBytes(data);
    (0, _assert_js_1.abytes)(data);
    return data;
}
/**
 * Copies several Uint8Arrays into one.
 */
function concatBytes(...arrays) {
    let sum = 0;
    for (let i = 0; i < arrays.length; i++) {
        const a = arrays[i];
        (0, _assert_js_1.abytes)(a);
        sum += a.length;
    }
    const res = new Uint8Array(sum);
    for (let i = 0, pad = 0; i < arrays.length; i++) {
        const a = arrays[i];
        res.set(a, pad);
        pad += a.length;
    }
    return res;
}
// For runtime check if class implements interface
class Hash {
    // Safe version that clones internal state
    clone() {
        return this._cloneInto();
    }
}
exports.Hash = Hash;
function checkOpts(defaults, opts) {
    if (opts !== undefined && {}.toString.call(opts) !== '[object Object]')
        throw new Error('Options should be object or undefined');
    const merged = Object.assign(defaults, opts);
    return merged;
}
function wrapConstructor(hashCons) {
    const hashC = (msg) => hashCons().update(toBytes(msg)).digest();
    const tmp = hashCons();
    hashC.outputLen = tmp.outputLen;
    hashC.blockLen = tmp.blockLen;
    hashC.create = () => hashCons();
    return hashC;
}
function wrapConstructorWithOpts(hashCons) {
    const hashC = (msg, opts) => hashCons(opts).update(toBytes(msg)).digest();
    const tmp = hashCons({});
    hashC.outputLen = tmp.outputLen;
    hashC.blockLen = tmp.blockLen;
    hashC.create = (opts) => hashCons(opts);
    return hashC;
}
function wrapXOFConstructorWithOpts(hashCons) {
    const hashC = (msg, opts) => hashCons(opts).update(toBytes(msg)).digest();
    const tmp = hashCons({});
    hashC.outputLen = tmp.outputLen;
    hashC.blockLen = tmp.blockLen;
    hashC.create = (opts) => hashCons(opts);
    return hashC;
}
/**
 * Secure PRNG. Uses `crypto.getRandomValues`, which defers to OS.
 */
function randomBytes(bytesLength = 32) {
    if (crypto_1.crypto && typeof crypto_1.crypto.getRandomValues === 'function') {
        return crypto_1.crypto.getRandomValues(new Uint8Array(bytesLength));
    }
    // Legacy Node.js compatibility
    if (crypto_1.crypto && typeof crypto_1.crypto.randomBytes === 'function') {
        return crypto_1.crypto.randomBytes(bytesLength);
    }
    throw new Error('crypto.getRandomValues must be defined');
}


/***/ }),

/***/ "../../node_modules/@noble/curves/secp256k1.js":
/*!*****************************************************!*\
  !*** ../../node_modules/@noble/curves/secp256k1.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.encodeToCurve = exports.hashToCurve = exports.schnorr = exports.secp256k1 = void 0;
/*! noble-curves - MIT License (c) 2022 Paul Miller (paulmillr.com) */
const sha256_1 = __webpack_require__(/*! @noble/hashes/sha256 */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/sha256.js");
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/curves/node_modules/@noble/hashes/utils.js");
const _shortw_utils_js_1 = __webpack_require__(/*! ./_shortw_utils.js */ "../../node_modules/@noble/curves/_shortw_utils.js");
const hash_to_curve_js_1 = __webpack_require__(/*! ./abstract/hash-to-curve.js */ "../../node_modules/@noble/curves/abstract/hash-to-curve.js");
const modular_js_1 = __webpack_require__(/*! ./abstract/modular.js */ "../../node_modules/@noble/curves/abstract/modular.js");
const utils_js_1 = __webpack_require__(/*! ./abstract/utils.js */ "../../node_modules/@noble/curves/abstract/utils.js");
const weierstrass_js_1 = __webpack_require__(/*! ./abstract/weierstrass.js */ "../../node_modules/@noble/curves/abstract/weierstrass.js");
const secp256k1P = BigInt('0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffffc2f');
const secp256k1N = BigInt('0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141');
const _1n = BigInt(1);
const _2n = BigInt(2);
const divNearest = (a, b) => (a + b / _2n) / b;
/**
 * √n = n^((p+1)/4) for fields p = 3 mod 4. We unwrap the loop and multiply bit-by-bit.
 * (P+1n/4n).toString(2) would produce bits [223x 1, 0, 22x 1, 4x 0, 11, 00]
 */
function sqrtMod(y) {
    const P = secp256k1P;
    // prettier-ignore
    const _3n = BigInt(3), _6n = BigInt(6), _11n = BigInt(11), _22n = BigInt(22);
    // prettier-ignore
    const _23n = BigInt(23), _44n = BigInt(44), _88n = BigInt(88);
    const b2 = (y * y * y) % P; // x^3, 11
    const b3 = (b2 * b2 * y) % P; // x^7
    const b6 = ((0, modular_js_1.pow2)(b3, _3n, P) * b3) % P;
    const b9 = ((0, modular_js_1.pow2)(b6, _3n, P) * b3) % P;
    const b11 = ((0, modular_js_1.pow2)(b9, _2n, P) * b2) % P;
    const b22 = ((0, modular_js_1.pow2)(b11, _11n, P) * b11) % P;
    const b44 = ((0, modular_js_1.pow2)(b22, _22n, P) * b22) % P;
    const b88 = ((0, modular_js_1.pow2)(b44, _44n, P) * b44) % P;
    const b176 = ((0, modular_js_1.pow2)(b88, _88n, P) * b88) % P;
    const b220 = ((0, modular_js_1.pow2)(b176, _44n, P) * b44) % P;
    const b223 = ((0, modular_js_1.pow2)(b220, _3n, P) * b3) % P;
    const t1 = ((0, modular_js_1.pow2)(b223, _23n, P) * b22) % P;
    const t2 = ((0, modular_js_1.pow2)(t1, _6n, P) * b2) % P;
    const root = (0, modular_js_1.pow2)(t2, _2n, P);
    if (!Fpk1.eql(Fpk1.sqr(root), y))
        throw new Error('Cannot find square root');
    return root;
}
const Fpk1 = (0, modular_js_1.Field)(secp256k1P, undefined, undefined, { sqrt: sqrtMod });
/**
 * secp256k1 short weierstrass curve and ECDSA signatures over it.
 */
exports.secp256k1 = (0, _shortw_utils_js_1.createCurve)({
    a: BigInt(0), // equation params: a, b
    b: BigInt(7), // Seem to be rigid: bitcointalk.org/index.php?topic=289795.msg3183975#msg3183975
    Fp: Fpk1, // Field's prime: 2n**256n - 2n**32n - 2n**9n - 2n**8n - 2n**7n - 2n**6n - 2n**4n - 1n
    n: secp256k1N, // Curve order, total count of valid points in the field
    // Base point (x, y) aka generator point
    Gx: BigInt('55066263022277343669578718895168534326250603453777594175500187360389116729240'),
    Gy: BigInt('32670510020758816978083085130507043184471273380659243275938904335757337482424'),
    h: BigInt(1), // Cofactor
    lowS: true, // Allow only low-S signatures by default in sign() and verify()
    /**
     * secp256k1 belongs to Koblitz curves: it has efficiently computable endomorphism.
     * Endomorphism uses 2x less RAM, speeds up precomputation by 2x and ECDH / key recovery by 20%.
     * For precomputed wNAF it trades off 1/2 init time & 1/3 ram for 20% perf hit.
     * Explanation: https://gist.github.com/paulmillr/eb670806793e84df628a7c434a873066
     */
    endo: {
        beta: BigInt('0x7ae96a2b657c07106e64479eac3434e99cf0497512f58995c1396c28719501ee'),
        splitScalar: (k) => {
            const n = secp256k1N;
            const a1 = BigInt('0x3086d221a7d46bcde86c90e49284eb15');
            const b1 = -_1n * BigInt('0xe4437ed6010e88286f547fa90abfe4c3');
            const a2 = BigInt('0x114ca50f7a8e2f3f657c1108d9d44cfd8');
            const b2 = a1;
            const POW_2_128 = BigInt('0x100000000000000000000000000000000'); // (2n**128n).toString(16)
            const c1 = divNearest(b2 * k, n);
            const c2 = divNearest(-b1 * k, n);
            let k1 = (0, modular_js_1.mod)(k - c1 * a1 - c2 * a2, n);
            let k2 = (0, modular_js_1.mod)(-c1 * b1 - c2 * b2, n);
            const k1neg = k1 > POW_2_128;
            const k2neg = k2 > POW_2_128;
            if (k1neg)
                k1 = n - k1;
            if (k2neg)
                k2 = n - k2;
            if (k1 > POW_2_128 || k2 > POW_2_128) {
                throw new Error('splitScalar: Endomorphism failed, k=' + k);
            }
            return { k1neg, k1, k2neg, k2 };
        },
    },
}, sha256_1.sha256);
// Schnorr signatures are superior to ECDSA from above. Below is Schnorr-specific BIP0340 code.
// https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki
const _0n = BigInt(0);
/** An object mapping tags to their tagged hash prefix of [SHA256(tag) | SHA256(tag)] */
const TAGGED_HASH_PREFIXES = {};
function taggedHash(tag, ...messages) {
    let tagP = TAGGED_HASH_PREFIXES[tag];
    if (tagP === undefined) {
        const tagH = (0, sha256_1.sha256)(Uint8Array.from(tag, (c) => c.charCodeAt(0)));
        tagP = (0, utils_js_1.concatBytes)(tagH, tagH);
        TAGGED_HASH_PREFIXES[tag] = tagP;
    }
    return (0, sha256_1.sha256)((0, utils_js_1.concatBytes)(tagP, ...messages));
}
// ECDSA compact points are 33-byte. Schnorr is 32: we strip first byte 0x02 or 0x03
const pointToBytes = (point) => point.toRawBytes(true).slice(1);
const numTo32b = (n) => (0, utils_js_1.numberToBytesBE)(n, 32);
const modP = (x) => (0, modular_js_1.mod)(x, secp256k1P);
const modN = (x) => (0, modular_js_1.mod)(x, secp256k1N);
const Point = exports.secp256k1.ProjectivePoint;
const GmulAdd = (Q, a, b) => Point.BASE.multiplyAndAddUnsafe(Q, a, b);
// Calculate point, scalar and bytes
function schnorrGetExtPubKey(priv) {
    let d_ = exports.secp256k1.utils.normPrivateKeyToScalar(priv); // same method executed in fromPrivateKey
    let p = Point.fromPrivateKey(d_); // P = d'⋅G; 0 < d' < n check is done inside
    const scalar = p.hasEvenY() ? d_ : modN(-d_);
    return { scalar: scalar, bytes: pointToBytes(p) };
}
/**
 * lift_x from BIP340. Convert 32-byte x coordinate to elliptic curve point.
 * @returns valid point checked for being on-curve
 */
function lift_x(x) {
    (0, utils_js_1.aInRange)('x', x, _1n, secp256k1P); // Fail if x ≥ p.
    const xx = modP(x * x);
    const c = modP(xx * x + BigInt(7)); // Let c = x³ + 7 mod p.
    let y = sqrtMod(c); // Let y = c^(p+1)/4 mod p.
    if (y % _2n !== _0n)
        y = modP(-y); // Return the unique point P such that x(P) = x and
    const p = new Point(x, y, _1n); // y(P) = y if y mod 2 = 0 or y(P) = p-y otherwise.
    p.assertValidity();
    return p;
}
const num = utils_js_1.bytesToNumberBE;
/**
 * Create tagged hash, convert it to bigint, reduce modulo-n.
 */
function challenge(...args) {
    return modN(num(taggedHash('BIP0340/challenge', ...args)));
}
/**
 * Schnorr public key is just `x` coordinate of Point as per BIP340.
 */
function schnorrGetPublicKey(privateKey) {
    return schnorrGetExtPubKey(privateKey).bytes; // d'=int(sk). Fail if d'=0 or d'≥n. Ret bytes(d'⋅G)
}
/**
 * Creates Schnorr signature as per BIP340. Verifies itself before returning anything.
 * auxRand is optional and is not the sole source of k generation: bad CSPRNG won't be dangerous.
 */
function schnorrSign(message, privateKey, auxRand = (0, utils_1.randomBytes)(32)) {
    const m = (0, utils_js_1.ensureBytes)('message', message);
    const { bytes: px, scalar: d } = schnorrGetExtPubKey(privateKey); // checks for isWithinCurveOrder
    const a = (0, utils_js_1.ensureBytes)('auxRand', auxRand, 32); // Auxiliary random data a: a 32-byte array
    const t = numTo32b(d ^ num(taggedHash('BIP0340/aux', a))); // Let t be the byte-wise xor of bytes(d) and hash/aux(a)
    const rand = taggedHash('BIP0340/nonce', t, px, m); // Let rand = hash/nonce(t || bytes(P) || m)
    const k_ = modN(num(rand)); // Let k' = int(rand) mod n
    if (k_ === _0n)
        throw new Error('sign failed: k is zero'); // Fail if k' = 0.
    const { bytes: rx, scalar: k } = schnorrGetExtPubKey(k_); // Let R = k'⋅G.
    const e = challenge(rx, px, m); // Let e = int(hash/challenge(bytes(R) || bytes(P) || m)) mod n.
    const sig = new Uint8Array(64); // Let sig = bytes(R) || bytes((k + ed) mod n).
    sig.set(rx, 0);
    sig.set(numTo32b(modN(k + e * d)), 32);
    // If Verify(bytes(P), m, sig) (see below) returns failure, abort
    if (!schnorrVerify(sig, m, px))
        throw new Error('sign: Invalid signature produced');
    return sig;
}
/**
 * Verifies Schnorr signature.
 * Will swallow errors & return false except for initial type validation of arguments.
 */
function schnorrVerify(signature, message, publicKey) {
    const sig = (0, utils_js_1.ensureBytes)('signature', signature, 64);
    const m = (0, utils_js_1.ensureBytes)('message', message);
    const pub = (0, utils_js_1.ensureBytes)('publicKey', publicKey, 32);
    try {
        const P = lift_x(num(pub)); // P = lift_x(int(pk)); fail if that fails
        const r = num(sig.subarray(0, 32)); // Let r = int(sig[0:32]); fail if r ≥ p.
        if (!(0, utils_js_1.inRange)(r, _1n, secp256k1P))
            return false;
        const s = num(sig.subarray(32, 64)); // Let s = int(sig[32:64]); fail if s ≥ n.
        if (!(0, utils_js_1.inRange)(s, _1n, secp256k1N))
            return false;
        const e = challenge(numTo32b(r), pointToBytes(P), m); // int(challenge(bytes(r)||bytes(P)||m))%n
        const R = GmulAdd(P, s, modN(-e)); // R = s⋅G - e⋅P
        if (!R || !R.hasEvenY() || R.toAffine().x !== r)
            return false; // -eP == (n-e)P
        return true; // Fail if is_infinite(R) / not has_even_y(R) / x(R) ≠ r.
    }
    catch (error) {
        return false;
    }
}
/**
 * Schnorr signatures over secp256k1.
 */
exports.schnorr = (() => ({
    getPublicKey: schnorrGetPublicKey,
    sign: schnorrSign,
    verify: schnorrVerify,
    utils: {
        randomPrivateKey: exports.secp256k1.utils.randomPrivateKey,
        lift_x,
        pointToBytes,
        numberToBytesBE: utils_js_1.numberToBytesBE,
        bytesToNumberBE: utils_js_1.bytesToNumberBE,
        taggedHash,
        mod: modular_js_1.mod,
    },
}))();
const isoMap = /* @__PURE__ */ (() => (0, hash_to_curve_js_1.isogenyMap)(Fpk1, [
    // xNum
    [
        '0x8e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38daaaaa8c7',
        '0x7d3d4c80bc321d5b9f315cea7fd44c5d595d2fc0bf63b92dfff1044f17c6581',
        '0x534c328d23f234e6e2a413deca25caece4506144037c40314ecbd0b53d9dd262',
        '0x8e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38e38daaaaa88c',
    ],
    // xDen
    [
        '0xd35771193d94918a9ca34ccbb7b640dd86cd409542f8487d9fe6b745781eb49b',
        '0xedadc6f64383dc1df7c4b2d51b54225406d36b641f5e41bbc52a56612a8c6d14',
        '0x0000000000000000000000000000000000000000000000000000000000000001', // LAST 1
    ],
    // yNum
    [
        '0x4bda12f684bda12f684bda12f684bda12f684bda12f684bda12f684b8e38e23c',
        '0xc75e0c32d5cb7c0fa9d0a54b12a0a6d5647ab046d686da6fdffc90fc201d71a3',
        '0x29a6194691f91a73715209ef6512e576722830a201be2018a765e85a9ecee931',
        '0x2f684bda12f684bda12f684bda12f684bda12f684bda12f684bda12f38e38d84',
    ],
    // yDen
    [
        '0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffefffff93b',
        '0x7a06534bb8bdb49fd5e9e6632722c2989467c1bfc8e8d978dfb425d2685c2573',
        '0x6484aa716545ca2cf3a70c3fa8fe337e0a3d21162f0d6299a7bf8192bfd2a76f',
        '0x0000000000000000000000000000000000000000000000000000000000000001', // LAST 1
    ],
].map((i) => i.map((j) => BigInt(j)))))();
const mapSWU = /* @__PURE__ */ (() => (0, weierstrass_js_1.mapToCurveSimpleSWU)(Fpk1, {
    A: BigInt('0x3f8731abdd661adca08a5558f0f5d272e953d363cb6f0e5d405447c01a444533'),
    B: BigInt('1771'),
    Z: Fpk1.create(BigInt('-11')),
}))();
const htf = /* @__PURE__ */ (() => (0, hash_to_curve_js_1.createHasher)(exports.secp256k1.ProjectivePoint, (scalars) => {
    const { x, y } = mapSWU(Fpk1.create(scalars[0]));
    return isoMap(x, y);
}, {
    DST: 'secp256k1_XMD:SHA-256_SSWU_RO_',
    encodeDST: 'secp256k1_XMD:SHA-256_SSWU_NU_',
    p: Fpk1.ORDER,
    m: 1,
    k: 128,
    expand: 'xmd',
    hash: sha256_1.sha256,
}))();
exports.hashToCurve = (() => htf.hashToCurve)();
exports.encodeToCurve = (() => htf.encodeToCurve)();


/***/ }),

/***/ "../../node_modules/@noble/hashes/_assert.js":
/*!***************************************************!*\
  !*** ../../node_modules/@noble/hashes/_assert.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.anumber = anumber;
exports.number = anumber;
exports.abytes = abytes;
exports.bytes = abytes;
exports.ahash = ahash;
exports.aexists = aexists;
exports.aoutput = aoutput;
function anumber(n) {
    if (!Number.isSafeInteger(n) || n < 0)
        throw new Error('positive integer expected, got ' + n);
}
// copied from utils
function isBytes(a) {
    return a instanceof Uint8Array || (ArrayBuffer.isView(a) && a.constructor.name === 'Uint8Array');
}
function abytes(b, ...lengths) {
    if (!isBytes(b))
        throw new Error('Uint8Array expected');
    if (lengths.length > 0 && !lengths.includes(b.length))
        throw new Error('Uint8Array expected of length ' + lengths + ', got length=' + b.length);
}
function ahash(h) {
    if (typeof h !== 'function' || typeof h.create !== 'function')
        throw new Error('Hash should be wrapped by utils.wrapConstructor');
    anumber(h.outputLen);
    anumber(h.blockLen);
}
function aexists(instance, checkFinished = true) {
    if (instance.destroyed)
        throw new Error('Hash instance has been destroyed');
    if (checkFinished && instance.finished)
        throw new Error('Hash#digest() has already been called');
}
function aoutput(out, instance) {
    abytes(out);
    const min = instance.outputLen;
    if (out.length < min) {
        throw new Error('digestInto() expects output buffer of length at least ' + min);
    }
}
const assert = {
    number: anumber,
    bytes: abytes,
    hash: ahash,
    exists: aexists,
    output: aoutput,
};
exports["default"] = assert;


/***/ }),

/***/ "../../node_modules/@noble/hashes/_md.js":
/*!***********************************************!*\
  !*** ../../node_modules/@noble/hashes/_md.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.HashMD = exports.Maj = exports.Chi = void 0;
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/hashes/_assert.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/hashes/utils.js");
/**
 * Polyfill for Safari 14
 */
function setBigUint64(view, byteOffset, value, isLE) {
    if (typeof view.setBigUint64 === 'function')
        return view.setBigUint64(byteOffset, value, isLE);
    const _32n = BigInt(32);
    const _u32_max = BigInt(0xffffffff);
    const wh = Number((value >> _32n) & _u32_max);
    const wl = Number(value & _u32_max);
    const h = isLE ? 4 : 0;
    const l = isLE ? 0 : 4;
    view.setUint32(byteOffset + h, wh, isLE);
    view.setUint32(byteOffset + l, wl, isLE);
}
/**
 * Choice: a ? b : c
 */
const Chi = (a, b, c) => (a & b) ^ (~a & c);
exports.Chi = Chi;
/**
 * Majority function, true if any two inputs is true
 */
const Maj = (a, b, c) => (a & b) ^ (a & c) ^ (b & c);
exports.Maj = Maj;
/**
 * Merkle-Damgard hash construction base class.
 * Could be used to create MD5, RIPEMD, SHA1, SHA2.
 */
class HashMD extends utils_js_1.Hash {
    constructor(blockLen, outputLen, padOffset, isLE) {
        super();
        this.blockLen = blockLen;
        this.outputLen = outputLen;
        this.padOffset = padOffset;
        this.isLE = isLE;
        this.finished = false;
        this.length = 0;
        this.pos = 0;
        this.destroyed = false;
        this.buffer = new Uint8Array(blockLen);
        this.view = (0, utils_js_1.createView)(this.buffer);
    }
    update(data) {
        (0, _assert_js_1.aexists)(this);
        const { view, buffer, blockLen } = this;
        data = (0, utils_js_1.toBytes)(data);
        const len = data.length;
        for (let pos = 0; pos < len;) {
            const take = Math.min(blockLen - this.pos, len - pos);
            // Fast path: we have at least one block in input, cast it to view and process
            if (take === blockLen) {
                const dataView = (0, utils_js_1.createView)(data);
                for (; blockLen <= len - pos; pos += blockLen)
                    this.process(dataView, pos);
                continue;
            }
            buffer.set(data.subarray(pos, pos + take), this.pos);
            this.pos += take;
            pos += take;
            if (this.pos === blockLen) {
                this.process(view, 0);
                this.pos = 0;
            }
        }
        this.length += data.length;
        this.roundClean();
        return this;
    }
    digestInto(out) {
        (0, _assert_js_1.aexists)(this);
        (0, _assert_js_1.aoutput)(out, this);
        this.finished = true;
        // Padding
        // We can avoid allocation of buffer for padding completely if it
        // was previously not allocated here. But it won't change performance.
        const { buffer, view, blockLen, isLE } = this;
        let { pos } = this;
        // append the bit '1' to the message
        buffer[pos++] = 0b10000000;
        this.buffer.subarray(pos).fill(0);
        // we have less than padOffset left in buffer, so we cannot put length in
        // current block, need process it and pad again
        if (this.padOffset > blockLen - pos) {
            this.process(view, 0);
            pos = 0;
        }
        // Pad until full block byte with zeros
        for (let i = pos; i < blockLen; i++)
            buffer[i] = 0;
        // Note: sha512 requires length to be 128bit integer, but length in JS will overflow before that
        // You need to write around 2 exabytes (u64_max / 8 / (1024**6)) for this to happen.
        // So we just write lowest 64 bits of that value.
        setBigUint64(view, blockLen - 8, BigInt(this.length * 8), isLE);
        this.process(view, 0);
        const oview = (0, utils_js_1.createView)(out);
        const len = this.outputLen;
        // NOTE: we do division by 4 later, which should be fused in single op with modulo by JIT
        if (len % 4)
            throw new Error('_sha2: outputLen should be aligned to 32bit');
        const outLen = len / 4;
        const state = this.get();
        if (outLen > state.length)
            throw new Error('_sha2: outputLen bigger than state');
        for (let i = 0; i < outLen; i++)
            oview.setUint32(4 * i, state[i], isLE);
    }
    digest() {
        const { buffer, outputLen } = this;
        this.digestInto(buffer);
        const res = buffer.slice(0, outputLen);
        this.destroy();
        return res;
    }
    _cloneInto(to) {
        to || (to = new this.constructor());
        to.set(...this.get());
        const { blockLen, buffer, length, finished, destroyed, pos } = this;
        to.length = length;
        to.pos = pos;
        to.finished = finished;
        to.destroyed = destroyed;
        if (length % blockLen)
            to.buffer.set(buffer);
        return to;
    }
}
exports.HashMD = HashMD;


/***/ }),

/***/ "../../node_modules/@noble/hashes/_u64.js":
/*!************************************************!*\
  !*** ../../node_modules/@noble/hashes/_u64.js ***!
  \************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.add5L = exports.add5H = exports.add4H = exports.add4L = exports.add3H = exports.add3L = exports.rotlBL = exports.rotlBH = exports.rotlSL = exports.rotlSH = exports.rotr32L = exports.rotr32H = exports.rotrBL = exports.rotrBH = exports.rotrSL = exports.rotrSH = exports.shrSL = exports.shrSH = exports.toBig = void 0;
exports.fromBig = fromBig;
exports.split = split;
exports.add = add;
const U32_MASK64 = /* @__PURE__ */ BigInt(2 ** 32 - 1);
const _32n = /* @__PURE__ */ BigInt(32);
// BigUint64Array is too slow as per 2024, so we implement it using Uint32Array.
// TODO: re-check https://issues.chromium.org/issues/42212588
function fromBig(n, le = false) {
    if (le)
        return { h: Number(n & U32_MASK64), l: Number((n >> _32n) & U32_MASK64) };
    return { h: Number((n >> _32n) & U32_MASK64) | 0, l: Number(n & U32_MASK64) | 0 };
}
function split(lst, le = false) {
    let Ah = new Uint32Array(lst.length);
    let Al = new Uint32Array(lst.length);
    for (let i = 0; i < lst.length; i++) {
        const { h, l } = fromBig(lst[i], le);
        [Ah[i], Al[i]] = [h, l];
    }
    return [Ah, Al];
}
const toBig = (h, l) => (BigInt(h >>> 0) << _32n) | BigInt(l >>> 0);
exports.toBig = toBig;
// for Shift in [0, 32)
const shrSH = (h, _l, s) => h >>> s;
exports.shrSH = shrSH;
const shrSL = (h, l, s) => (h << (32 - s)) | (l >>> s);
exports.shrSL = shrSL;
// Right rotate for Shift in [1, 32)
const rotrSH = (h, l, s) => (h >>> s) | (l << (32 - s));
exports.rotrSH = rotrSH;
const rotrSL = (h, l, s) => (h << (32 - s)) | (l >>> s);
exports.rotrSL = rotrSL;
// Right rotate for Shift in (32, 64), NOTE: 32 is special case.
const rotrBH = (h, l, s) => (h << (64 - s)) | (l >>> (s - 32));
exports.rotrBH = rotrBH;
const rotrBL = (h, l, s) => (h >>> (s - 32)) | (l << (64 - s));
exports.rotrBL = rotrBL;
// Right rotate for shift===32 (just swaps l&h)
const rotr32H = (_h, l) => l;
exports.rotr32H = rotr32H;
const rotr32L = (h, _l) => h;
exports.rotr32L = rotr32L;
// Left rotate for Shift in [1, 32)
const rotlSH = (h, l, s) => (h << s) | (l >>> (32 - s));
exports.rotlSH = rotlSH;
const rotlSL = (h, l, s) => (l << s) | (h >>> (32 - s));
exports.rotlSL = rotlSL;
// Left rotate for Shift in (32, 64), NOTE: 32 is special case.
const rotlBH = (h, l, s) => (l << (s - 32)) | (h >>> (64 - s));
exports.rotlBH = rotlBH;
const rotlBL = (h, l, s) => (h << (s - 32)) | (l >>> (64 - s));
exports.rotlBL = rotlBL;
// JS uses 32-bit signed integers for bitwise operations which means we cannot
// simple take carry out of low bit sum by shift, we need to use division.
function add(Ah, Al, Bh, Bl) {
    const l = (Al >>> 0) + (Bl >>> 0);
    return { h: (Ah + Bh + ((l / 2 ** 32) | 0)) | 0, l: l | 0 };
}
// Addition with more than 2 elements
const add3L = (Al, Bl, Cl) => (Al >>> 0) + (Bl >>> 0) + (Cl >>> 0);
exports.add3L = add3L;
const add3H = (low, Ah, Bh, Ch) => (Ah + Bh + Ch + ((low / 2 ** 32) | 0)) | 0;
exports.add3H = add3H;
const add4L = (Al, Bl, Cl, Dl) => (Al >>> 0) + (Bl >>> 0) + (Cl >>> 0) + (Dl >>> 0);
exports.add4L = add4L;
const add4H = (low, Ah, Bh, Ch, Dh) => (Ah + Bh + Ch + Dh + ((low / 2 ** 32) | 0)) | 0;
exports.add4H = add4H;
const add5L = (Al, Bl, Cl, Dl, El) => (Al >>> 0) + (Bl >>> 0) + (Cl >>> 0) + (Dl >>> 0) + (El >>> 0);
exports.add5L = add5L;
const add5H = (low, Ah, Bh, Ch, Dh, Eh) => (Ah + Bh + Ch + Dh + Eh + ((low / 2 ** 32) | 0)) | 0;
exports.add5H = add5H;
// prettier-ignore
const u64 = {
    fromBig, split, toBig,
    shrSH, shrSL,
    rotrSH, rotrSL, rotrBH, rotrBL,
    rotr32H, rotr32L,
    rotlSH, rotlSL, rotlBH, rotlBL,
    add, add3L, add3H, add4L, add4H, add5H, add5L,
};
exports["default"] = u64;


/***/ }),

/***/ "../../node_modules/@noble/hashes/crypto.js":
/*!**************************************************!*\
  !*** ../../node_modules/@noble/hashes/crypto.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.crypto = void 0;
exports.crypto = typeof globalThis === 'object' && 'crypto' in globalThis ? globalThis.crypto : undefined;


/***/ }),

/***/ "../../node_modules/@noble/hashes/hmac.js":
/*!************************************************!*\
  !*** ../../node_modules/@noble/hashes/hmac.js ***!
  \************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.hmac = exports.HMAC = void 0;
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/hashes/_assert.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/hashes/utils.js");
// HMAC (RFC 2104)
class HMAC extends utils_js_1.Hash {
    constructor(hash, _key) {
        super();
        this.finished = false;
        this.destroyed = false;
        (0, _assert_js_1.ahash)(hash);
        const key = (0, utils_js_1.toBytes)(_key);
        this.iHash = hash.create();
        if (typeof this.iHash.update !== 'function')
            throw new Error('Expected instance of class which extends utils.Hash');
        this.blockLen = this.iHash.blockLen;
        this.outputLen = this.iHash.outputLen;
        const blockLen = this.blockLen;
        const pad = new Uint8Array(blockLen);
        // blockLen can be bigger than outputLen
        pad.set(key.length > blockLen ? hash.create().update(key).digest() : key);
        for (let i = 0; i < pad.length; i++)
            pad[i] ^= 0x36;
        this.iHash.update(pad);
        // By doing update (processing of first block) of outer hash here we can re-use it between multiple calls via clone
        this.oHash = hash.create();
        // Undo internal XOR && apply outer XOR
        for (let i = 0; i < pad.length; i++)
            pad[i] ^= 0x36 ^ 0x5c;
        this.oHash.update(pad);
        pad.fill(0);
    }
    update(buf) {
        (0, _assert_js_1.aexists)(this);
        this.iHash.update(buf);
        return this;
    }
    digestInto(out) {
        (0, _assert_js_1.aexists)(this);
        (0, _assert_js_1.abytes)(out, this.outputLen);
        this.finished = true;
        this.iHash.digestInto(out);
        this.oHash.update(out);
        this.oHash.digestInto(out);
        this.destroy();
    }
    digest() {
        const out = new Uint8Array(this.oHash.outputLen);
        this.digestInto(out);
        return out;
    }
    _cloneInto(to) {
        // Create new instance without calling constructor since key already in state and we don't know it.
        to || (to = Object.create(Object.getPrototypeOf(this), {}));
        const { oHash, iHash, finished, destroyed, blockLen, outputLen } = this;
        to = to;
        to.finished = finished;
        to.destroyed = destroyed;
        to.blockLen = blockLen;
        to.outputLen = outputLen;
        to.oHash = oHash._cloneInto(to.oHash);
        to.iHash = iHash._cloneInto(to.iHash);
        return to;
    }
    destroy() {
        this.destroyed = true;
        this.oHash.destroy();
        this.iHash.destroy();
    }
}
exports.HMAC = HMAC;
/**
 * HMAC: RFC2104 message authentication code.
 * @param hash - function that would be used e.g. sha256
 * @param key - message key
 * @param message - message data
 * @example
 * import { hmac } from '@noble/hashes/hmac';
 * import { sha256 } from '@noble/hashes/sha2';
 * const mac1 = hmac(sha256, 'key', 'message');
 */
const hmac = (hash, key, message) => new HMAC(hash, key).update(message).digest();
exports.hmac = hmac;
exports.hmac.create = (hash, key) => new HMAC(hash, key);


/***/ }),

/***/ "../../node_modules/@noble/hashes/pbkdf2.js":
/*!**************************************************!*\
  !*** ../../node_modules/@noble/hashes/pbkdf2.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.pbkdf2 = pbkdf2;
exports.pbkdf2Async = pbkdf2Async;
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/hashes/_assert.js");
const hmac_js_1 = __webpack_require__(/*! ./hmac.js */ "../../node_modules/@noble/hashes/hmac.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/hashes/utils.js");
// Common prologue and epilogue for sync/async functions
function pbkdf2Init(hash, _password, _salt, _opts) {
    (0, _assert_js_1.ahash)(hash);
    const opts = (0, utils_js_1.checkOpts)({ dkLen: 32, asyncTick: 10 }, _opts);
    const { c, dkLen, asyncTick } = opts;
    (0, _assert_js_1.anumber)(c);
    (0, _assert_js_1.anumber)(dkLen);
    (0, _assert_js_1.anumber)(asyncTick);
    if (c < 1)
        throw new Error('PBKDF2: iterations (c) should be >= 1');
    const password = (0, utils_js_1.toBytes)(_password);
    const salt = (0, utils_js_1.toBytes)(_salt);
    // DK = PBKDF2(PRF, Password, Salt, c, dkLen);
    const DK = new Uint8Array(dkLen);
    // U1 = PRF(Password, Salt + INT_32_BE(i))
    const PRF = hmac_js_1.hmac.create(hash, password);
    const PRFSalt = PRF._cloneInto().update(salt);
    return { c, dkLen, asyncTick, DK, PRF, PRFSalt };
}
function pbkdf2Output(PRF, PRFSalt, DK, prfW, u) {
    PRF.destroy();
    PRFSalt.destroy();
    if (prfW)
        prfW.destroy();
    u.fill(0);
    return DK;
}
/**
 * PBKDF2-HMAC: RFC 2898 key derivation function
 * @param hash - hash function that would be used e.g. sha256
 * @param password - password from which a derived key is generated
 * @param salt - cryptographic salt
 * @param opts - {c, dkLen} where c is work factor and dkLen is output message size
 */
function pbkdf2(hash, password, salt, opts) {
    const { c, dkLen, DK, PRF, PRFSalt } = pbkdf2Init(hash, password, salt, opts);
    let prfW; // Working copy
    const arr = new Uint8Array(4);
    const view = (0, utils_js_1.createView)(arr);
    const u = new Uint8Array(PRF.outputLen);
    // DK = T1 + T2 + ⋯ + Tdklen/hlen
    for (let ti = 1, pos = 0; pos < dkLen; ti++, pos += PRF.outputLen) {
        // Ti = F(Password, Salt, c, i)
        const Ti = DK.subarray(pos, pos + PRF.outputLen);
        view.setInt32(0, ti, false);
        // F(Password, Salt, c, i) = U1 ^ U2 ^ ⋯ ^ Uc
        // U1 = PRF(Password, Salt + INT_32_BE(i))
        (prfW = PRFSalt._cloneInto(prfW)).update(arr).digestInto(u);
        Ti.set(u.subarray(0, Ti.length));
        for (let ui = 1; ui < c; ui++) {
            // Uc = PRF(Password, Uc−1)
            PRF._cloneInto(prfW).update(u).digestInto(u);
            for (let i = 0; i < Ti.length; i++)
                Ti[i] ^= u[i];
        }
    }
    return pbkdf2Output(PRF, PRFSalt, DK, prfW, u);
}
async function pbkdf2Async(hash, password, salt, opts) {
    const { c, dkLen, asyncTick, DK, PRF, PRFSalt } = pbkdf2Init(hash, password, salt, opts);
    let prfW; // Working copy
    const arr = new Uint8Array(4);
    const view = (0, utils_js_1.createView)(arr);
    const u = new Uint8Array(PRF.outputLen);
    // DK = T1 + T2 + ⋯ + Tdklen/hlen
    for (let ti = 1, pos = 0; pos < dkLen; ti++, pos += PRF.outputLen) {
        // Ti = F(Password, Salt, c, i)
        const Ti = DK.subarray(pos, pos + PRF.outputLen);
        view.setInt32(0, ti, false);
        // F(Password, Salt, c, i) = U1 ^ U2 ^ ⋯ ^ Uc
        // U1 = PRF(Password, Salt + INT_32_BE(i))
        (prfW = PRFSalt._cloneInto(prfW)).update(arr).digestInto(u);
        Ti.set(u.subarray(0, Ti.length));
        await (0, utils_js_1.asyncLoop)(c - 1, asyncTick, () => {
            // Uc = PRF(Password, Uc−1)
            PRF._cloneInto(prfW).update(u).digestInto(u);
            for (let i = 0; i < Ti.length; i++)
                Ti[i] ^= u[i];
        });
    }
    return pbkdf2Output(PRF, PRFSalt, DK, prfW, u);
}


/***/ }),

/***/ "../../node_modules/@noble/hashes/ripemd160.js":
/*!*****************************************************!*\
  !*** ../../node_modules/@noble/hashes/ripemd160.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.ripemd160 = exports.RIPEMD160 = void 0;
const _md_js_1 = __webpack_require__(/*! ./_md.js */ "../../node_modules/@noble/hashes/_md.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/hashes/utils.js");
// https://homes.esat.kuleuven.be/~bosselae/ripemd160.html
// https://homes.esat.kuleuven.be/~bosselae/ripemd160/pdf/AB-9601/AB-9601.pdf
const Rho = /* @__PURE__ */ new Uint8Array([7, 4, 13, 1, 10, 6, 15, 3, 12, 0, 9, 5, 2, 14, 11, 8]);
const Id = /* @__PURE__ */ new Uint8Array(new Array(16).fill(0).map((_, i) => i));
const Pi = /* @__PURE__ */ Id.map((i) => (9 * i + 5) % 16);
let idxL = [Id];
let idxR = [Pi];
for (let i = 0; i < 4; i++)
    for (let j of [idxL, idxR])
        j.push(j[i].map((k) => Rho[k]));
const shifts = /* @__PURE__ */ [
    [11, 14, 15, 12, 5, 8, 7, 9, 11, 13, 14, 15, 6, 7, 9, 8],
    [12, 13, 11, 15, 6, 9, 9, 7, 12, 15, 11, 13, 7, 8, 7, 7],
    [13, 15, 14, 11, 7, 7, 6, 8, 13, 14, 13, 12, 5, 5, 6, 9],
    [14, 11, 12, 14, 8, 6, 5, 5, 15, 12, 15, 14, 9, 9, 8, 6],
    [15, 12, 13, 13, 9, 5, 8, 6, 14, 11, 12, 11, 8, 6, 5, 5],
].map((i) => new Uint8Array(i));
const shiftsL = /* @__PURE__ */ idxL.map((idx, i) => idx.map((j) => shifts[i][j]));
const shiftsR = /* @__PURE__ */ idxR.map((idx, i) => idx.map((j) => shifts[i][j]));
const Kl = /* @__PURE__ */ new Uint32Array([
    0x00000000, 0x5a827999, 0x6ed9eba1, 0x8f1bbcdc, 0xa953fd4e,
]);
const Kr = /* @__PURE__ */ new Uint32Array([
    0x50a28be6, 0x5c4dd124, 0x6d703ef3, 0x7a6d76e9, 0x00000000,
]);
// It's called f() in spec.
function f(group, x, y, z) {
    if (group === 0)
        return x ^ y ^ z;
    else if (group === 1)
        return (x & y) | (~x & z);
    else if (group === 2)
        return (x | ~y) ^ z;
    else if (group === 3)
        return (x & z) | (y & ~z);
    else
        return x ^ (y | ~z);
}
// Temporary buffer, not used to store anything between runs
const R_BUF = /* @__PURE__ */ new Uint32Array(16);
class RIPEMD160 extends _md_js_1.HashMD {
    constructor() {
        super(64, 20, 8, true);
        this.h0 = 0x67452301 | 0;
        this.h1 = 0xefcdab89 | 0;
        this.h2 = 0x98badcfe | 0;
        this.h3 = 0x10325476 | 0;
        this.h4 = 0xc3d2e1f0 | 0;
    }
    get() {
        const { h0, h1, h2, h3, h4 } = this;
        return [h0, h1, h2, h3, h4];
    }
    set(h0, h1, h2, h3, h4) {
        this.h0 = h0 | 0;
        this.h1 = h1 | 0;
        this.h2 = h2 | 0;
        this.h3 = h3 | 0;
        this.h4 = h4 | 0;
    }
    process(view, offset) {
        for (let i = 0; i < 16; i++, offset += 4)
            R_BUF[i] = view.getUint32(offset, true);
        // prettier-ignore
        let al = this.h0 | 0, ar = al, bl = this.h1 | 0, br = bl, cl = this.h2 | 0, cr = cl, dl = this.h3 | 0, dr = dl, el = this.h4 | 0, er = el;
        // Instead of iterating 0 to 80, we split it into 5 groups
        // And use the groups in constants, functions, etc. Much simpler
        for (let group = 0; group < 5; group++) {
            const rGroup = 4 - group;
            const hbl = Kl[group], hbr = Kr[group]; // prettier-ignore
            const rl = idxL[group], rr = idxR[group]; // prettier-ignore
            const sl = shiftsL[group], sr = shiftsR[group]; // prettier-ignore
            for (let i = 0; i < 16; i++) {
                const tl = ((0, utils_js_1.rotl)(al + f(group, bl, cl, dl) + R_BUF[rl[i]] + hbl, sl[i]) + el) | 0;
                al = el, el = dl, dl = (0, utils_js_1.rotl)(cl, 10) | 0, cl = bl, bl = tl; // prettier-ignore
            }
            // 2 loops are 10% faster
            for (let i = 0; i < 16; i++) {
                const tr = ((0, utils_js_1.rotl)(ar + f(rGroup, br, cr, dr) + R_BUF[rr[i]] + hbr, sr[i]) + er) | 0;
                ar = er, er = dr, dr = (0, utils_js_1.rotl)(cr, 10) | 0, cr = br, br = tr; // prettier-ignore
            }
        }
        // Add the compressed chunk to the current hash value
        this.set((this.h1 + cl + dr) | 0, (this.h2 + dl + er) | 0, (this.h3 + el + ar) | 0, (this.h4 + al + br) | 0, (this.h0 + bl + cr) | 0);
    }
    roundClean() {
        R_BUF.fill(0);
    }
    destroy() {
        this.destroyed = true;
        this.buffer.fill(0);
        this.set(0, 0, 0, 0, 0);
    }
}
exports.RIPEMD160 = RIPEMD160;
/**
 * RIPEMD-160 - a hash function from 1990s.
 * @param message - msg that would be hashed
 */
exports.ripemd160 = (0, utils_js_1.wrapConstructor)(() => new RIPEMD160());


/***/ }),

/***/ "../../node_modules/@noble/hashes/sha256.js":
/*!**************************************************!*\
  !*** ../../node_modules/@noble/hashes/sha256.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sha224 = exports.sha256 = exports.SHA256 = void 0;
const _md_js_1 = __webpack_require__(/*! ./_md.js */ "../../node_modules/@noble/hashes/_md.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/hashes/utils.js");
// SHA2-256 need to try 2^128 hashes to execute birthday attack.
// BTC network is doing 2^70 hashes/sec (2^95 hashes/year) as per late 2024.
// Round constants:
// first 32 bits of the fractional parts of the cube roots of the first 64 primes 2..311)
// prettier-ignore
const SHA256_K = /* @__PURE__ */ new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
]);
// Initial state:
// first 32 bits of the fractional parts of the square roots of the first 8 primes 2..19
// prettier-ignore
const SHA256_IV = /* @__PURE__ */ new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19
]);
// Temporary buffer, not used to store anything between runs
// Named this way because it matches specification.
const SHA256_W = /* @__PURE__ */ new Uint32Array(64);
class SHA256 extends _md_js_1.HashMD {
    constructor() {
        super(64, 32, 8, false);
        // We cannot use array here since array allows indexing by variable
        // which means optimizer/compiler cannot use registers.
        this.A = SHA256_IV[0] | 0;
        this.B = SHA256_IV[1] | 0;
        this.C = SHA256_IV[2] | 0;
        this.D = SHA256_IV[3] | 0;
        this.E = SHA256_IV[4] | 0;
        this.F = SHA256_IV[5] | 0;
        this.G = SHA256_IV[6] | 0;
        this.H = SHA256_IV[7] | 0;
    }
    get() {
        const { A, B, C, D, E, F, G, H } = this;
        return [A, B, C, D, E, F, G, H];
    }
    // prettier-ignore
    set(A, B, C, D, E, F, G, H) {
        this.A = A | 0;
        this.B = B | 0;
        this.C = C | 0;
        this.D = D | 0;
        this.E = E | 0;
        this.F = F | 0;
        this.G = G | 0;
        this.H = H | 0;
    }
    process(view, offset) {
        // Extend the first 16 words into the remaining 48 words w[16..63] of the message schedule array
        for (let i = 0; i < 16; i++, offset += 4)
            SHA256_W[i] = view.getUint32(offset, false);
        for (let i = 16; i < 64; i++) {
            const W15 = SHA256_W[i - 15];
            const W2 = SHA256_W[i - 2];
            const s0 = (0, utils_js_1.rotr)(W15, 7) ^ (0, utils_js_1.rotr)(W15, 18) ^ (W15 >>> 3);
            const s1 = (0, utils_js_1.rotr)(W2, 17) ^ (0, utils_js_1.rotr)(W2, 19) ^ (W2 >>> 10);
            SHA256_W[i] = (s1 + SHA256_W[i - 7] + s0 + SHA256_W[i - 16]) | 0;
        }
        // Compression function main loop, 64 rounds
        let { A, B, C, D, E, F, G, H } = this;
        for (let i = 0; i < 64; i++) {
            const sigma1 = (0, utils_js_1.rotr)(E, 6) ^ (0, utils_js_1.rotr)(E, 11) ^ (0, utils_js_1.rotr)(E, 25);
            const T1 = (H + sigma1 + (0, _md_js_1.Chi)(E, F, G) + SHA256_K[i] + SHA256_W[i]) | 0;
            const sigma0 = (0, utils_js_1.rotr)(A, 2) ^ (0, utils_js_1.rotr)(A, 13) ^ (0, utils_js_1.rotr)(A, 22);
            const T2 = (sigma0 + (0, _md_js_1.Maj)(A, B, C)) | 0;
            H = G;
            G = F;
            F = E;
            E = (D + T1) | 0;
            D = C;
            C = B;
            B = A;
            A = (T1 + T2) | 0;
        }
        // Add the compressed chunk to the current hash value
        A = (A + this.A) | 0;
        B = (B + this.B) | 0;
        C = (C + this.C) | 0;
        D = (D + this.D) | 0;
        E = (E + this.E) | 0;
        F = (F + this.F) | 0;
        G = (G + this.G) | 0;
        H = (H + this.H) | 0;
        this.set(A, B, C, D, E, F, G, H);
    }
    roundClean() {
        SHA256_W.fill(0);
    }
    destroy() {
        this.set(0, 0, 0, 0, 0, 0, 0, 0);
        this.buffer.fill(0);
    }
}
exports.SHA256 = SHA256;
// Constants from https://nvlpubs.nist.gov/nistpubs/FIPS/NIST.FIPS.180-4.pdf
class SHA224 extends SHA256 {
    constructor() {
        super();
        this.A = 0xc1059ed8 | 0;
        this.B = 0x367cd507 | 0;
        this.C = 0x3070dd17 | 0;
        this.D = 0xf70e5939 | 0;
        this.E = 0xffc00b31 | 0;
        this.F = 0x68581511 | 0;
        this.G = 0x64f98fa7 | 0;
        this.H = 0xbefa4fa4 | 0;
        this.outputLen = 28;
    }
}
/**
 * SHA2-256 hash function
 * @param message - data that would be hashed
 */
exports.sha256 = (0, utils_js_1.wrapConstructor)(() => new SHA256());
/**
 * SHA2-224 hash function
 */
exports.sha224 = (0, utils_js_1.wrapConstructor)(() => new SHA224());


/***/ }),

/***/ "../../node_modules/@noble/hashes/sha512.js":
/*!**************************************************!*\
  !*** ../../node_modules/@noble/hashes/sha512.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sha384 = exports.sha512_256 = exports.sha512_224 = exports.sha512 = exports.SHA384 = exports.SHA512_256 = exports.SHA512_224 = exports.SHA512 = void 0;
const _md_js_1 = __webpack_require__(/*! ./_md.js */ "../../node_modules/@noble/hashes/_md.js");
const _u64_js_1 = __webpack_require__(/*! ./_u64.js */ "../../node_modules/@noble/hashes/_u64.js");
const utils_js_1 = __webpack_require__(/*! ./utils.js */ "../../node_modules/@noble/hashes/utils.js");
// Round contants (first 32 bits of the fractional parts of the cube roots of the first 80 primes 2..409):
// prettier-ignore
const [SHA512_Kh, SHA512_Kl] = /* @__PURE__ */ (() => _u64_js_1.default.split([
    '0x428a2f98d728ae22', '0x7137449123ef65cd', '0xb5c0fbcfec4d3b2f', '0xe9b5dba58189dbbc',
    '0x3956c25bf348b538', '0x59f111f1b605d019', '0x923f82a4af194f9b', '0xab1c5ed5da6d8118',
    '0xd807aa98a3030242', '0x12835b0145706fbe', '0x243185be4ee4b28c', '0x550c7dc3d5ffb4e2',
    '0x72be5d74f27b896f', '0x80deb1fe3b1696b1', '0x9bdc06a725c71235', '0xc19bf174cf692694',
    '0xe49b69c19ef14ad2', '0xefbe4786384f25e3', '0x0fc19dc68b8cd5b5', '0x240ca1cc77ac9c65',
    '0x2de92c6f592b0275', '0x4a7484aa6ea6e483', '0x5cb0a9dcbd41fbd4', '0x76f988da831153b5',
    '0x983e5152ee66dfab', '0xa831c66d2db43210', '0xb00327c898fb213f', '0xbf597fc7beef0ee4',
    '0xc6e00bf33da88fc2', '0xd5a79147930aa725', '0x06ca6351e003826f', '0x142929670a0e6e70',
    '0x27b70a8546d22ffc', '0x2e1b21385c26c926', '0x4d2c6dfc5ac42aed', '0x53380d139d95b3df',
    '0x650a73548baf63de', '0x766a0abb3c77b2a8', '0x81c2c92e47edaee6', '0x92722c851482353b',
    '0xa2bfe8a14cf10364', '0xa81a664bbc423001', '0xc24b8b70d0f89791', '0xc76c51a30654be30',
    '0xd192e819d6ef5218', '0xd69906245565a910', '0xf40e35855771202a', '0x106aa07032bbd1b8',
    '0x19a4c116b8d2d0c8', '0x1e376c085141ab53', '0x2748774cdf8eeb99', '0x34b0bcb5e19b48a8',
    '0x391c0cb3c5c95a63', '0x4ed8aa4ae3418acb', '0x5b9cca4f7763e373', '0x682e6ff3d6b2b8a3',
    '0x748f82ee5defb2fc', '0x78a5636f43172f60', '0x84c87814a1f0ab72', '0x8cc702081a6439ec',
    '0x90befffa23631e28', '0xa4506cebde82bde9', '0xbef9a3f7b2c67915', '0xc67178f2e372532b',
    '0xca273eceea26619c', '0xd186b8c721c0c207', '0xeada7dd6cde0eb1e', '0xf57d4f7fee6ed178',
    '0x06f067aa72176fba', '0x0a637dc5a2c898a6', '0x113f9804bef90dae', '0x1b710b35131c471b',
    '0x28db77f523047d84', '0x32caab7b40c72493', '0x3c9ebe0a15c9bebc', '0x431d67c49c100d4c',
    '0x4cc5d4becb3e42b6', '0x597f299cfc657e2a', '0x5fcb6fab3ad6faec', '0x6c44198c4a475817'
].map(n => BigInt(n))))();
// Temporary buffer, not used to store anything between runs
const SHA512_W_H = /* @__PURE__ */ new Uint32Array(80);
const SHA512_W_L = /* @__PURE__ */ new Uint32Array(80);
class SHA512 extends _md_js_1.HashMD {
    constructor() {
        super(128, 64, 16, false);
        // We cannot use array here since array allows indexing by variable which means optimizer/compiler cannot use registers.
        // Also looks cleaner and easier to verify with spec.
        // Initial state (first 32 bits of the fractional parts of the square roots of the first 8 primes 2..19):
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0x6a09e667 | 0;
        this.Al = 0xf3bcc908 | 0;
        this.Bh = 0xbb67ae85 | 0;
        this.Bl = 0x84caa73b | 0;
        this.Ch = 0x3c6ef372 | 0;
        this.Cl = 0xfe94f82b | 0;
        this.Dh = 0xa54ff53a | 0;
        this.Dl = 0x5f1d36f1 | 0;
        this.Eh = 0x510e527f | 0;
        this.El = 0xade682d1 | 0;
        this.Fh = 0x9b05688c | 0;
        this.Fl = 0x2b3e6c1f | 0;
        this.Gh = 0x1f83d9ab | 0;
        this.Gl = 0xfb41bd6b | 0;
        this.Hh = 0x5be0cd19 | 0;
        this.Hl = 0x137e2179 | 0;
    }
    // prettier-ignore
    get() {
        const { Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl } = this;
        return [Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl];
    }
    // prettier-ignore
    set(Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl) {
        this.Ah = Ah | 0;
        this.Al = Al | 0;
        this.Bh = Bh | 0;
        this.Bl = Bl | 0;
        this.Ch = Ch | 0;
        this.Cl = Cl | 0;
        this.Dh = Dh | 0;
        this.Dl = Dl | 0;
        this.Eh = Eh | 0;
        this.El = El | 0;
        this.Fh = Fh | 0;
        this.Fl = Fl | 0;
        this.Gh = Gh | 0;
        this.Gl = Gl | 0;
        this.Hh = Hh | 0;
        this.Hl = Hl | 0;
    }
    process(view, offset) {
        // Extend the first 16 words into the remaining 64 words w[16..79] of the message schedule array
        for (let i = 0; i < 16; i++, offset += 4) {
            SHA512_W_H[i] = view.getUint32(offset);
            SHA512_W_L[i] = view.getUint32((offset += 4));
        }
        for (let i = 16; i < 80; i++) {
            // s0 := (w[i-15] rightrotate 1) xor (w[i-15] rightrotate 8) xor (w[i-15] rightshift 7)
            const W15h = SHA512_W_H[i - 15] | 0;
            const W15l = SHA512_W_L[i - 15] | 0;
            const s0h = _u64_js_1.default.rotrSH(W15h, W15l, 1) ^ _u64_js_1.default.rotrSH(W15h, W15l, 8) ^ _u64_js_1.default.shrSH(W15h, W15l, 7);
            const s0l = _u64_js_1.default.rotrSL(W15h, W15l, 1) ^ _u64_js_1.default.rotrSL(W15h, W15l, 8) ^ _u64_js_1.default.shrSL(W15h, W15l, 7);
            // s1 := (w[i-2] rightrotate 19) xor (w[i-2] rightrotate 61) xor (w[i-2] rightshift 6)
            const W2h = SHA512_W_H[i - 2] | 0;
            const W2l = SHA512_W_L[i - 2] | 0;
            const s1h = _u64_js_1.default.rotrSH(W2h, W2l, 19) ^ _u64_js_1.default.rotrBH(W2h, W2l, 61) ^ _u64_js_1.default.shrSH(W2h, W2l, 6);
            const s1l = _u64_js_1.default.rotrSL(W2h, W2l, 19) ^ _u64_js_1.default.rotrBL(W2h, W2l, 61) ^ _u64_js_1.default.shrSL(W2h, W2l, 6);
            // SHA256_W[i] = s0 + s1 + SHA256_W[i - 7] + SHA256_W[i - 16];
            const SUMl = _u64_js_1.default.add4L(s0l, s1l, SHA512_W_L[i - 7], SHA512_W_L[i - 16]);
            const SUMh = _u64_js_1.default.add4H(SUMl, s0h, s1h, SHA512_W_H[i - 7], SHA512_W_H[i - 16]);
            SHA512_W_H[i] = SUMh | 0;
            SHA512_W_L[i] = SUMl | 0;
        }
        let { Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl } = this;
        // Compression function main loop, 80 rounds
        for (let i = 0; i < 80; i++) {
            // S1 := (e rightrotate 14) xor (e rightrotate 18) xor (e rightrotate 41)
            const sigma1h = _u64_js_1.default.rotrSH(Eh, El, 14) ^ _u64_js_1.default.rotrSH(Eh, El, 18) ^ _u64_js_1.default.rotrBH(Eh, El, 41);
            const sigma1l = _u64_js_1.default.rotrSL(Eh, El, 14) ^ _u64_js_1.default.rotrSL(Eh, El, 18) ^ _u64_js_1.default.rotrBL(Eh, El, 41);
            //const T1 = (H + sigma1 + Chi(E, F, G) + SHA256_K[i] + SHA256_W[i]) | 0;
            const CHIh = (Eh & Fh) ^ (~Eh & Gh);
            const CHIl = (El & Fl) ^ (~El & Gl);
            // T1 = H + sigma1 + Chi(E, F, G) + SHA512_K[i] + SHA512_W[i]
            // prettier-ignore
            const T1ll = _u64_js_1.default.add5L(Hl, sigma1l, CHIl, SHA512_Kl[i], SHA512_W_L[i]);
            const T1h = _u64_js_1.default.add5H(T1ll, Hh, sigma1h, CHIh, SHA512_Kh[i], SHA512_W_H[i]);
            const T1l = T1ll | 0;
            // S0 := (a rightrotate 28) xor (a rightrotate 34) xor (a rightrotate 39)
            const sigma0h = _u64_js_1.default.rotrSH(Ah, Al, 28) ^ _u64_js_1.default.rotrBH(Ah, Al, 34) ^ _u64_js_1.default.rotrBH(Ah, Al, 39);
            const sigma0l = _u64_js_1.default.rotrSL(Ah, Al, 28) ^ _u64_js_1.default.rotrBL(Ah, Al, 34) ^ _u64_js_1.default.rotrBL(Ah, Al, 39);
            const MAJh = (Ah & Bh) ^ (Ah & Ch) ^ (Bh & Ch);
            const MAJl = (Al & Bl) ^ (Al & Cl) ^ (Bl & Cl);
            Hh = Gh | 0;
            Hl = Gl | 0;
            Gh = Fh | 0;
            Gl = Fl | 0;
            Fh = Eh | 0;
            Fl = El | 0;
            ({ h: Eh, l: El } = _u64_js_1.default.add(Dh | 0, Dl | 0, T1h | 0, T1l | 0));
            Dh = Ch | 0;
            Dl = Cl | 0;
            Ch = Bh | 0;
            Cl = Bl | 0;
            Bh = Ah | 0;
            Bl = Al | 0;
            const All = _u64_js_1.default.add3L(T1l, sigma0l, MAJl);
            Ah = _u64_js_1.default.add3H(All, T1h, sigma0h, MAJh);
            Al = All | 0;
        }
        // Add the compressed chunk to the current hash value
        ({ h: Ah, l: Al } = _u64_js_1.default.add(this.Ah | 0, this.Al | 0, Ah | 0, Al | 0));
        ({ h: Bh, l: Bl } = _u64_js_1.default.add(this.Bh | 0, this.Bl | 0, Bh | 0, Bl | 0));
        ({ h: Ch, l: Cl } = _u64_js_1.default.add(this.Ch | 0, this.Cl | 0, Ch | 0, Cl | 0));
        ({ h: Dh, l: Dl } = _u64_js_1.default.add(this.Dh | 0, this.Dl | 0, Dh | 0, Dl | 0));
        ({ h: Eh, l: El } = _u64_js_1.default.add(this.Eh | 0, this.El | 0, Eh | 0, El | 0));
        ({ h: Fh, l: Fl } = _u64_js_1.default.add(this.Fh | 0, this.Fl | 0, Fh | 0, Fl | 0));
        ({ h: Gh, l: Gl } = _u64_js_1.default.add(this.Gh | 0, this.Gl | 0, Gh | 0, Gl | 0));
        ({ h: Hh, l: Hl } = _u64_js_1.default.add(this.Hh | 0, this.Hl | 0, Hh | 0, Hl | 0));
        this.set(Ah, Al, Bh, Bl, Ch, Cl, Dh, Dl, Eh, El, Fh, Fl, Gh, Gl, Hh, Hl);
    }
    roundClean() {
        SHA512_W_H.fill(0);
        SHA512_W_L.fill(0);
    }
    destroy() {
        this.buffer.fill(0);
        this.set(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);
    }
}
exports.SHA512 = SHA512;
class SHA512_224 extends SHA512 {
    constructor() {
        super();
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0x8c3d37c8 | 0;
        this.Al = 0x19544da2 | 0;
        this.Bh = 0x73e19966 | 0;
        this.Bl = 0x89dcd4d6 | 0;
        this.Ch = 0x1dfab7ae | 0;
        this.Cl = 0x32ff9c82 | 0;
        this.Dh = 0x679dd514 | 0;
        this.Dl = 0x582f9fcf | 0;
        this.Eh = 0x0f6d2b69 | 0;
        this.El = 0x7bd44da8 | 0;
        this.Fh = 0x77e36f73 | 0;
        this.Fl = 0x04c48942 | 0;
        this.Gh = 0x3f9d85a8 | 0;
        this.Gl = 0x6a1d36c8 | 0;
        this.Hh = 0x1112e6ad | 0;
        this.Hl = 0x91d692a1 | 0;
        this.outputLen = 28;
    }
}
exports.SHA512_224 = SHA512_224;
class SHA512_256 extends SHA512 {
    constructor() {
        super();
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0x22312194 | 0;
        this.Al = 0xfc2bf72c | 0;
        this.Bh = 0x9f555fa3 | 0;
        this.Bl = 0xc84c64c2 | 0;
        this.Ch = 0x2393b86b | 0;
        this.Cl = 0x6f53b151 | 0;
        this.Dh = 0x96387719 | 0;
        this.Dl = 0x5940eabd | 0;
        this.Eh = 0x96283ee2 | 0;
        this.El = 0xa88effe3 | 0;
        this.Fh = 0xbe5e1e25 | 0;
        this.Fl = 0x53863992 | 0;
        this.Gh = 0x2b0199fc | 0;
        this.Gl = 0x2c85b8aa | 0;
        this.Hh = 0x0eb72ddc | 0;
        this.Hl = 0x81c52ca2 | 0;
        this.outputLen = 32;
    }
}
exports.SHA512_256 = SHA512_256;
class SHA384 extends SHA512 {
    constructor() {
        super();
        // h -- high 32 bits, l -- low 32 bits
        this.Ah = 0xcbbb9d5d | 0;
        this.Al = 0xc1059ed8 | 0;
        this.Bh = 0x629a292a | 0;
        this.Bl = 0x367cd507 | 0;
        this.Ch = 0x9159015a | 0;
        this.Cl = 0x3070dd17 | 0;
        this.Dh = 0x152fecd8 | 0;
        this.Dl = 0xf70e5939 | 0;
        this.Eh = 0x67332667 | 0;
        this.El = 0xffc00b31 | 0;
        this.Fh = 0x8eb44a87 | 0;
        this.Fl = 0x68581511 | 0;
        this.Gh = 0xdb0c2e0d | 0;
        this.Gl = 0x64f98fa7 | 0;
        this.Hh = 0x47b5481d | 0;
        this.Hl = 0xbefa4fa4 | 0;
        this.outputLen = 48;
    }
}
exports.SHA384 = SHA384;
exports.sha512 = (0, utils_js_1.wrapConstructor)(() => new SHA512());
exports.sha512_224 = (0, utils_js_1.wrapConstructor)(() => new SHA512_224());
exports.sha512_256 = (0, utils_js_1.wrapConstructor)(() => new SHA512_256());
exports.sha384 = (0, utils_js_1.wrapConstructor)(() => new SHA384());


/***/ }),

/***/ "../../node_modules/@noble/hashes/utils.js":
/*!*************************************************!*\
  !*** ../../node_modules/@noble/hashes/utils.js ***!
  \*************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

/*! noble-hashes - MIT License (c) 2022 Paul Miller (paulmillr.com) */
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash = exports.nextTick = exports.byteSwapIfBE = exports.byteSwap = exports.isLE = exports.rotl = exports.rotr = exports.createView = exports.u32 = exports.u8 = void 0;
exports.isBytes = isBytes;
exports.byteSwap32 = byteSwap32;
exports.bytesToHex = bytesToHex;
exports.hexToBytes = hexToBytes;
exports.asyncLoop = asyncLoop;
exports.utf8ToBytes = utf8ToBytes;
exports.toBytes = toBytes;
exports.concatBytes = concatBytes;
exports.checkOpts = checkOpts;
exports.wrapConstructor = wrapConstructor;
exports.wrapConstructorWithOpts = wrapConstructorWithOpts;
exports.wrapXOFConstructorWithOpts = wrapXOFConstructorWithOpts;
exports.randomBytes = randomBytes;
// We use WebCrypto aka globalThis.crypto, which exists in browsers and node.js 16+.
// node.js versions earlier than v19 don't declare it in global scope.
// For node.js, package.json#exports field mapping rewrites import
// from `crypto` to `cryptoNode`, which imports native module.
// Makes the utils un-importable in browsers without a bundler.
// Once node.js 18 is deprecated (2025-04-30), we can just drop the import.
const crypto_1 = __webpack_require__(/*! @noble/hashes/crypto */ "../../node_modules/@noble/hashes/crypto.js");
const _assert_js_1 = __webpack_require__(/*! ./_assert.js */ "../../node_modules/@noble/hashes/_assert.js");
// export { isBytes } from './_assert.js';
// We can't reuse isBytes from _assert, because somehow this causes huge perf issues
function isBytes(a) {
    return a instanceof Uint8Array || (ArrayBuffer.isView(a) && a.constructor.name === 'Uint8Array');
}
// Cast array to different type
const u8 = (arr) => new Uint8Array(arr.buffer, arr.byteOffset, arr.byteLength);
exports.u8 = u8;
const u32 = (arr) => new Uint32Array(arr.buffer, arr.byteOffset, Math.floor(arr.byteLength / 4));
exports.u32 = u32;
// Cast array to view
const createView = (arr) => new DataView(arr.buffer, arr.byteOffset, arr.byteLength);
exports.createView = createView;
// The rotate right (circular right shift) operation for uint32
const rotr = (word, shift) => (word << (32 - shift)) | (word >>> shift);
exports.rotr = rotr;
// The rotate left (circular left shift) operation for uint32
const rotl = (word, shift) => (word << shift) | ((word >>> (32 - shift)) >>> 0);
exports.rotl = rotl;
exports.isLE = (() => new Uint8Array(new Uint32Array([0x11223344]).buffer)[0] === 0x44)();
// The byte swap operation for uint32
const byteSwap = (word) => ((word << 24) & 0xff000000) |
    ((word << 8) & 0xff0000) |
    ((word >>> 8) & 0xff00) |
    ((word >>> 24) & 0xff);
exports.byteSwap = byteSwap;
// Conditionally byte swap if on a big-endian platform
exports.byteSwapIfBE = exports.isLE ? (n) => n : (n) => (0, exports.byteSwap)(n);
// In place byte swap for Uint32Array
function byteSwap32(arr) {
    for (let i = 0; i < arr.length; i++) {
        arr[i] = (0, exports.byteSwap)(arr[i]);
    }
}
// Array where index 0xf0 (240) is mapped to string 'f0'
const hexes = /* @__PURE__ */ Array.from({ length: 256 }, (_, i) => i.toString(16).padStart(2, '0'));
/**
 * @example bytesToHex(Uint8Array.from([0xca, 0xfe, 0x01, 0x23])) // 'cafe0123'
 */
function bytesToHex(bytes) {
    (0, _assert_js_1.abytes)(bytes);
    // pre-caching improves the speed 6x
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
        hex += hexes[bytes[i]];
    }
    return hex;
}
// We use optimized technique to convert hex string to byte array
const asciis = { _0: 48, _9: 57, A: 65, F: 70, a: 97, f: 102 };
function asciiToBase16(ch) {
    if (ch >= asciis._0 && ch <= asciis._9)
        return ch - asciis._0; // '2' => 50-48
    if (ch >= asciis.A && ch <= asciis.F)
        return ch - (asciis.A - 10); // 'B' => 66-(65-10)
    if (ch >= asciis.a && ch <= asciis.f)
        return ch - (asciis.a - 10); // 'b' => 98-(97-10)
    return;
}
/**
 * @example hexToBytes('cafe0123') // Uint8Array.from([0xca, 0xfe, 0x01, 0x23])
 */
function hexToBytes(hex) {
    if (typeof hex !== 'string')
        throw new Error('hex string expected, got ' + typeof hex);
    const hl = hex.length;
    const al = hl / 2;
    if (hl % 2)
        throw new Error('hex string expected, got unpadded hex of length ' + hl);
    const array = new Uint8Array(al);
    for (let ai = 0, hi = 0; ai < al; ai++, hi += 2) {
        const n1 = asciiToBase16(hex.charCodeAt(hi));
        const n2 = asciiToBase16(hex.charCodeAt(hi + 1));
        if (n1 === undefined || n2 === undefined) {
            const char = hex[hi] + hex[hi + 1];
            throw new Error('hex string expected, got non-hex character "' + char + '" at index ' + hi);
        }
        array[ai] = n1 * 16 + n2; // multiply first octet, e.g. 'a3' => 10*16+3 => 160 + 3 => 163
    }
    return array;
}
// There is no setImmediate in browser and setTimeout is slow.
// call of async fn will return Promise, which will be fullfiled only on
// next scheduler queue processing step and this is exactly what we need.
const nextTick = async () => { };
exports.nextTick = nextTick;
// Returns control to thread each 'tick' ms to avoid blocking
async function asyncLoop(iters, tick, cb) {
    let ts = Date.now();
    for (let i = 0; i < iters; i++) {
        cb(i);
        // Date.now() is not monotonic, so in case if clock goes backwards we return return control too
        const diff = Date.now() - ts;
        if (diff >= 0 && diff < tick)
            continue;
        await (0, exports.nextTick)();
        ts += diff;
    }
}
/**
 * @example utf8ToBytes('abc') // new Uint8Array([97, 98, 99])
 */
function utf8ToBytes(str) {
    if (typeof str !== 'string')
        throw new Error('utf8ToBytes expected string, got ' + typeof str);
    return new Uint8Array(new TextEncoder().encode(str)); // https://bugzil.la/1681809
}
/**
 * Normalizes (non-hex) string or Uint8Array to Uint8Array.
 * Warning: when Uint8Array is passed, it would NOT get copied.
 * Keep in mind for future mutable operations.
 */
function toBytes(data) {
    if (typeof data === 'string')
        data = utf8ToBytes(data);
    (0, _assert_js_1.abytes)(data);
    return data;
}
/**
 * Copies several Uint8Arrays into one.
 */
function concatBytes(...arrays) {
    let sum = 0;
    for (let i = 0; i < arrays.length; i++) {
        const a = arrays[i];
        (0, _assert_js_1.abytes)(a);
        sum += a.length;
    }
    const res = new Uint8Array(sum);
    for (let i = 0, pad = 0; i < arrays.length; i++) {
        const a = arrays[i];
        res.set(a, pad);
        pad += a.length;
    }
    return res;
}
// For runtime check if class implements interface
class Hash {
    // Safe version that clones internal state
    clone() {
        return this._cloneInto();
    }
}
exports.Hash = Hash;
function checkOpts(defaults, opts) {
    if (opts !== undefined && {}.toString.call(opts) !== '[object Object]')
        throw new Error('Options should be object or undefined');
    const merged = Object.assign(defaults, opts);
    return merged;
}
function wrapConstructor(hashCons) {
    const hashC = (msg) => hashCons().update(toBytes(msg)).digest();
    const tmp = hashCons();
    hashC.outputLen = tmp.outputLen;
    hashC.blockLen = tmp.blockLen;
    hashC.create = () => hashCons();
    return hashC;
}
function wrapConstructorWithOpts(hashCons) {
    const hashC = (msg, opts) => hashCons(opts).update(toBytes(msg)).digest();
    const tmp = hashCons({});
    hashC.outputLen = tmp.outputLen;
    hashC.blockLen = tmp.blockLen;
    hashC.create = (opts) => hashCons(opts);
    return hashC;
}
function wrapXOFConstructorWithOpts(hashCons) {
    const hashC = (msg, opts) => hashCons(opts).update(toBytes(msg)).digest();
    const tmp = hashCons({});
    hashC.outputLen = tmp.outputLen;
    hashC.blockLen = tmp.blockLen;
    hashC.create = (opts) => hashCons(opts);
    return hashC;
}
/**
 * Secure PRNG. Uses `crypto.getRandomValues`, which defers to OS.
 */
function randomBytes(bytesLength = 32) {
    if (crypto_1.crypto && typeof crypto_1.crypto.getRandomValues === 'function') {
        return crypto_1.crypto.getRandomValues(new Uint8Array(bytesLength));
    }
    // Legacy Node.js compatibility
    if (crypto_1.crypto && typeof crypto_1.crypto.randomBytes === 'function') {
        return crypto_1.crypto.randomBytes(bytesLength);
    }
    throw new Error('crypto.getRandomValues must be defined');
}


/***/ }),

/***/ "../../node_modules/@scure/base/lib/index.js":
/*!***************************************************!*\
  !*** ../../node_modules/@scure/base/lib/index.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

/*! scure-base - MIT License (c) 2022 Paul Miller (paulmillr.com) */
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.bytes = exports.stringToBytes = exports.str = exports.bytesToString = exports.hex = exports.utf8 = exports.bech32m = exports.bech32 = exports.base58check = exports.createBase58check = exports.base58xmr = exports.base58xrp = exports.base58flickr = exports.base58 = exports.base64urlnopad = exports.base64url = exports.base64nopad = exports.base64 = exports.base32crockford = exports.base32hexnopad = exports.base32hex = exports.base32nopad = exports.base32 = exports.base16 = exports.utils = exports.assertNumber = void 0;
function isBytes(a) {
    return a instanceof Uint8Array || (ArrayBuffer.isView(a) && a.constructor.name === 'Uint8Array');
}
function isArrayOf(isString, arr) {
    if (!Array.isArray(arr))
        return false;
    if (arr.length === 0)
        return true;
    if (isString) {
        return arr.every((item) => typeof item === 'string');
    }
    else {
        return arr.every((item) => Number.isSafeInteger(item));
    }
}
// no abytes: seems to have 10% slowdown. Why?!
function afn(input) {
    if (typeof input !== 'function')
        throw new Error('function expected');
    return true;
}
function astr(label, input) {
    if (typeof input !== 'string')
        throw new Error(`${label}: string expected`);
    return true;
}
function anumber(n) {
    if (!Number.isSafeInteger(n))
        throw new Error(`invalid integer: ${n}`);
}
exports.assertNumber = anumber;
function aArr(input) {
    if (!Array.isArray(input))
        throw new Error('array expected');
}
function astrArr(label, input) {
    if (!isArrayOf(true, input))
        throw new Error(`${label}: array of strings expected`);
}
function anumArr(label, input) {
    if (!isArrayOf(false, input))
        throw new Error(`${label}: array of numbers expected`);
}
/**
 * @__NO_SIDE_EFFECTS__
 */
function chain(...args) {
    const id = (a) => a;
    // Wrap call in closure so JIT can inline calls
    const wrap = (a, b) => (c) => a(b(c));
    // Construct chain of args[-1].encode(args[-2].encode([...]))
    const encode = args.map((x) => x.encode).reduceRight(wrap, id);
    // Construct chain of args[0].decode(args[1].decode(...))
    const decode = args.map((x) => x.decode).reduce(wrap, id);
    return { encode, decode };
}
/**
 * Encodes integer radix representation to array of strings using alphabet and back.
 * Could also be array of strings.
 * @__NO_SIDE_EFFECTS__
 */
function alphabet(letters) {
    // mapping 1 to "b"
    const lettersA = typeof letters === 'string' ? letters.split('') : letters;
    const len = lettersA.length;
    astrArr('alphabet', lettersA);
    // mapping "b" to 1
    const indexes = new Map(lettersA.map((l, i) => [l, i]));
    return {
        encode: (digits) => {
            aArr(digits);
            return digits.map((i) => {
                if (!Number.isSafeInteger(i) || i < 0 || i >= len)
                    throw new Error(`alphabet.encode: digit index outside alphabet "${i}". Allowed: ${letters}`);
                return lettersA[i];
            });
        },
        decode: (input) => {
            aArr(input);
            return input.map((letter) => {
                astr('alphabet.decode', letter);
                const i = indexes.get(letter);
                if (i === undefined)
                    throw new Error(`Unknown letter: "${letter}". Allowed: ${letters}`);
                return i;
            });
        },
    };
}
/**
 * @__NO_SIDE_EFFECTS__
 */
function join(separator = '') {
    astr('join', separator);
    return {
        encode: (from) => {
            astrArr('join.decode', from);
            return from.join(separator);
        },
        decode: (to) => {
            astr('join.decode', to);
            return to.split(separator);
        },
    };
}
/**
 * Pad strings array so it has integer number of bits
 * @__NO_SIDE_EFFECTS__
 */
function padding(bits, chr = '=') {
    anumber(bits);
    astr('padding', chr);
    return {
        encode(data) {
            astrArr('padding.encode', data);
            while ((data.length * bits) % 8)
                data.push(chr);
            return data;
        },
        decode(input) {
            astrArr('padding.decode', input);
            let end = input.length;
            if ((end * bits) % 8)
                throw new Error('padding: invalid, string should have whole number of bytes');
            for (; end > 0 && input[end - 1] === chr; end--) {
                const last = end - 1;
                const byte = last * bits;
                if (byte % 8 === 0)
                    throw new Error('padding: invalid, string has too much padding');
            }
            return input.slice(0, end);
        },
    };
}
/**
 * @__NO_SIDE_EFFECTS__
 */
function normalize(fn) {
    afn(fn);
    return { encode: (from) => from, decode: (to) => fn(to) };
}
/**
 * Slow: O(n^2) time complexity
 */
function convertRadix(data, from, to) {
    // base 1 is impossible
    if (from < 2)
        throw new Error(`convertRadix: invalid from=${from}, base cannot be less than 2`);
    if (to < 2)
        throw new Error(`convertRadix: invalid to=${to}, base cannot be less than 2`);
    aArr(data);
    if (!data.length)
        return [];
    let pos = 0;
    const res = [];
    const digits = Array.from(data, (d) => {
        anumber(d);
        if (d < 0 || d >= from)
            throw new Error(`invalid integer: ${d}`);
        return d;
    });
    const dlen = digits.length;
    while (true) {
        let carry = 0;
        let done = true;
        for (let i = pos; i < dlen; i++) {
            const digit = digits[i];
            const fromCarry = from * carry;
            const digitBase = fromCarry + digit;
            if (!Number.isSafeInteger(digitBase) ||
                fromCarry / from !== carry ||
                digitBase - digit !== fromCarry) {
                throw new Error('convertRadix: carry overflow');
            }
            const div = digitBase / to;
            carry = digitBase % to;
            const rounded = Math.floor(div);
            digits[i] = rounded;
            if (!Number.isSafeInteger(rounded) || rounded * to + carry !== digitBase)
                throw new Error('convertRadix: carry overflow');
            if (!done)
                continue;
            else if (!rounded)
                pos = i;
            else
                done = false;
        }
        res.push(carry);
        if (done)
            break;
    }
    for (let i = 0; i < data.length - 1 && data[i] === 0; i++)
        res.push(0);
    return res.reverse();
}
const gcd = (a, b) => (b === 0 ? a : gcd(b, a % b));
const radix2carry = /* @__NO_SIDE_EFFECTS__ */ (from, to) => from + (to - gcd(from, to));
const powers = /* @__PURE__ */ (() => {
    let res = [];
    for (let i = 0; i < 40; i++)
        res.push(2 ** i);
    return res;
})();
/**
 * Implemented with numbers, because BigInt is 5x slower
 */
function convertRadix2(data, from, to, padding) {
    aArr(data);
    if (from <= 0 || from > 32)
        throw new Error(`convertRadix2: wrong from=${from}`);
    if (to <= 0 || to > 32)
        throw new Error(`convertRadix2: wrong to=${to}`);
    if (radix2carry(from, to) > 32) {
        throw new Error(`convertRadix2: carry overflow from=${from} to=${to} carryBits=${radix2carry(from, to)}`);
    }
    let carry = 0;
    let pos = 0; // bitwise position in current element
    const max = powers[from];
    const mask = powers[to] - 1;
    const res = [];
    for (const n of data) {
        anumber(n);
        if (n >= max)
            throw new Error(`convertRadix2: invalid data word=${n} from=${from}`);
        carry = (carry << from) | n;
        if (pos + from > 32)
            throw new Error(`convertRadix2: carry overflow pos=${pos} from=${from}`);
        pos += from;
        for (; pos >= to; pos -= to)
            res.push(((carry >> (pos - to)) & mask) >>> 0);
        const pow = powers[pos];
        if (pow === undefined)
            throw new Error('invalid carry');
        carry &= pow - 1; // clean carry, otherwise it will cause overflow
    }
    carry = (carry << (to - pos)) & mask;
    if (!padding && pos >= from)
        throw new Error('Excess padding');
    if (!padding && carry > 0)
        throw new Error(`Non-zero padding: ${carry}`);
    if (padding && pos > 0)
        res.push(carry >>> 0);
    return res;
}
/**
 * @__NO_SIDE_EFFECTS__
 */
function radix(num) {
    anumber(num);
    const _256 = 2 ** 8;
    return {
        encode: (bytes) => {
            if (!isBytes(bytes))
                throw new Error('radix.encode input should be Uint8Array');
            return convertRadix(Array.from(bytes), _256, num);
        },
        decode: (digits) => {
            anumArr('radix.decode', digits);
            return Uint8Array.from(convertRadix(digits, num, _256));
        },
    };
}
/**
 * If both bases are power of same number (like `2**8 <-> 2**64`),
 * there is a linear algorithm. For now we have implementation for power-of-two bases only.
 * @__NO_SIDE_EFFECTS__
 */
function radix2(bits, revPadding = false) {
    anumber(bits);
    if (bits <= 0 || bits > 32)
        throw new Error('radix2: bits should be in (0..32]');
    if (radix2carry(8, bits) > 32 || radix2carry(bits, 8) > 32)
        throw new Error('radix2: carry overflow');
    return {
        encode: (bytes) => {
            if (!isBytes(bytes))
                throw new Error('radix2.encode input should be Uint8Array');
            return convertRadix2(Array.from(bytes), 8, bits, !revPadding);
        },
        decode: (digits) => {
            anumArr('radix2.decode', digits);
            return Uint8Array.from(convertRadix2(digits, bits, 8, revPadding));
        },
    };
}
function unsafeWrapper(fn) {
    afn(fn);
    return function (...args) {
        try {
            return fn.apply(null, args);
        }
        catch (e) { }
    };
}
function checksum(len, fn) {
    anumber(len);
    afn(fn);
    return {
        encode(data) {
            if (!isBytes(data))
                throw new Error('checksum.encode: input should be Uint8Array');
            const sum = fn(data).slice(0, len);
            const res = new Uint8Array(data.length + len);
            res.set(data);
            res.set(sum, data.length);
            return res;
        },
        decode(data) {
            if (!isBytes(data))
                throw new Error('checksum.decode: input should be Uint8Array');
            const payload = data.slice(0, -len);
            const oldChecksum = data.slice(-len);
            const newChecksum = fn(payload).slice(0, len);
            for (let i = 0; i < len; i++)
                if (newChecksum[i] !== oldChecksum[i])
                    throw new Error('Invalid checksum');
            return payload;
        },
    };
}
// prettier-ignore
exports.utils = {
    alphabet, chain, checksum, convertRadix, convertRadix2, radix, radix2, join, padding,
};
// RFC 4648 aka RFC 3548
// ---------------------
/**
 * base16 encoding.
 */
exports.base16 = chain(radix2(4), alphabet('0123456789ABCDEF'), join(''));
exports.base32 = chain(radix2(5), alphabet('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'), padding(5), join(''));
exports.base32nopad = chain(radix2(5), alphabet('ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'), join(''));
exports.base32hex = chain(radix2(5), alphabet('0123456789ABCDEFGHIJKLMNOPQRSTUV'), padding(5), join(''));
exports.base32hexnopad = chain(radix2(5), alphabet('0123456789ABCDEFGHIJKLMNOPQRSTUV'), join(''));
exports.base32crockford = chain(radix2(5), alphabet('0123456789ABCDEFGHJKMNPQRSTVWXYZ'), join(''), normalize((s) => s.toUpperCase().replace(/O/g, '0').replace(/[IL]/g, '1')));
/**
 * base64 with padding. For no padding, use `base64nopad`.
 * @example
 * const b = base64.decode('A951'); // Uint8Array.from([ 3, 222, 117 ])
 * base64.encode(b); // 'A951'
 */
exports.base64 = chain(radix2(6), alphabet('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'), padding(6), join(''));
/**
 * base64 without padding.
 */
exports.base64nopad = chain(radix2(6), alphabet('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'), join(''));
exports.base64url = chain(radix2(6), alphabet('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'), padding(6), join(''));
exports.base64urlnopad = chain(radix2(6), alphabet('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_'), join(''));
// base58 code
// -----------
const genBase58 = /* @__NO_SIDE_EFFECTS__ */ (abc) => chain(radix(58), alphabet(abc), join(''));
/**
 * Base58: base64 without characters +, /, 0, O, I, l.
 * Quadratic (O(n^2)) - so, can't be used on large inputs.
 */
exports.base58 = genBase58('123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz');
exports.base58flickr = genBase58('123456789abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ');
exports.base58xrp = genBase58('rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz');
// Data len (index) -> encoded block len
const XMR_BLOCK_LEN = [0, 2, 3, 5, 6, 7, 9, 10, 11];
/**
 * XMR version of base58.
 * Done in 8-byte blocks (which equals 11 chars in decoding). Last (non-full) block padded with '1' to size in XMR_BLOCK_LEN.
 * Block encoding significantly reduces quadratic complexity of base58.
 */
exports.base58xmr = {
    encode(data) {
        let res = '';
        for (let i = 0; i < data.length; i += 8) {
            const block = data.subarray(i, i + 8);
            res += exports.base58.encode(block).padStart(XMR_BLOCK_LEN[block.length], '1');
        }
        return res;
    },
    decode(str) {
        let res = [];
        for (let i = 0; i < str.length; i += 11) {
            const slice = str.slice(i, i + 11);
            const blockLen = XMR_BLOCK_LEN.indexOf(slice.length);
            const block = exports.base58.decode(slice);
            for (let j = 0; j < block.length - blockLen; j++) {
                if (block[j] !== 0)
                    throw new Error('base58xmr: wrong padding');
            }
            res = res.concat(Array.from(block.slice(block.length - blockLen)));
        }
        return Uint8Array.from(res);
    },
};
const createBase58check = (sha256) => chain(checksum(4, (data) => sha256(sha256(data))), exports.base58);
exports.createBase58check = createBase58check;
/**
 * Use `createBase58check` instead.
 * @deprecated
 */
exports.base58check = exports.createBase58check;
const BECH_ALPHABET = chain(alphabet('qpzry9x8gf2tvdw0s3jn54khce6mua7l'), join(''));
const POLYMOD_GENERATORS = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3];
function bech32Polymod(pre) {
    const b = pre >> 25;
    let chk = (pre & 0x1ffffff) << 5;
    for (let i = 0; i < POLYMOD_GENERATORS.length; i++) {
        if (((b >> i) & 1) === 1)
            chk ^= POLYMOD_GENERATORS[i];
    }
    return chk;
}
function bechChecksum(prefix, words, encodingConst = 1) {
    const len = prefix.length;
    let chk = 1;
    for (let i = 0; i < len; i++) {
        const c = prefix.charCodeAt(i);
        if (c < 33 || c > 126)
            throw new Error(`Invalid prefix (${prefix})`);
        chk = bech32Polymod(chk) ^ (c >> 5);
    }
    chk = bech32Polymod(chk);
    for (let i = 0; i < len; i++)
        chk = bech32Polymod(chk) ^ (prefix.charCodeAt(i) & 0x1f);
    for (let v of words)
        chk = bech32Polymod(chk) ^ v;
    for (let i = 0; i < 6; i++)
        chk = bech32Polymod(chk);
    chk ^= encodingConst;
    return BECH_ALPHABET.encode(convertRadix2([chk % powers[30]], 30, 5, false));
}
/**
 * @__NO_SIDE_EFFECTS__
 */
function genBech32(encoding) {
    const ENCODING_CONST = encoding === 'bech32' ? 1 : 0x2bc830a3;
    const _words = radix2(5);
    const fromWords = _words.decode;
    const toWords = _words.encode;
    const fromWordsUnsafe = unsafeWrapper(fromWords);
    function encode(prefix, words, limit = 90) {
        astr('bech32.encode prefix', prefix);
        if (isBytes(words))
            words = Array.from(words);
        anumArr('bech32.encode', words);
        const plen = prefix.length;
        if (plen === 0)
            throw new TypeError(`Invalid prefix length ${plen}`);
        const actualLength = plen + 7 + words.length;
        if (limit !== false && actualLength > limit)
            throw new TypeError(`Length ${actualLength} exceeds limit ${limit}`);
        const lowered = prefix.toLowerCase();
        const sum = bechChecksum(lowered, words, ENCODING_CONST);
        return `${lowered}1${BECH_ALPHABET.encode(words)}${sum}`;
    }
    function decode(str, limit = 90) {
        astr('bech32.decode input', str);
        const slen = str.length;
        if (slen < 8 || (limit !== false && slen > limit))
            throw new TypeError(`invalid string length: ${slen} (${str}). Expected (8..${limit})`);
        // don't allow mixed case
        const lowered = str.toLowerCase();
        if (str !== lowered && str !== str.toUpperCase())
            throw new Error(`String must be lowercase or uppercase`);
        const sepIndex = lowered.lastIndexOf('1');
        if (sepIndex === 0 || sepIndex === -1)
            throw new Error(`Letter "1" must be present between prefix and data only`);
        const prefix = lowered.slice(0, sepIndex);
        const data = lowered.slice(sepIndex + 1);
        if (data.length < 6)
            throw new Error('Data must be at least 6 characters long');
        const words = BECH_ALPHABET.decode(data).slice(0, -6);
        const sum = bechChecksum(prefix, words, ENCODING_CONST);
        if (!data.endsWith(sum))
            throw new Error(`Invalid checksum in ${str}: expected "${sum}"`);
        return { prefix, words };
    }
    const decodeUnsafe = unsafeWrapper(decode);
    function decodeToBytes(str) {
        const { prefix, words } = decode(str, false);
        return { prefix, words, bytes: fromWords(words) };
    }
    function encodeFromBytes(prefix, bytes) {
        return encode(prefix, toWords(bytes));
    }
    return {
        encode,
        decode,
        encodeFromBytes,
        decodeToBytes,
        decodeUnsafe,
        fromWords,
        fromWordsUnsafe,
        toWords,
    };
}
/**
 * Low-level bech32 operations. Operates on words.
 */
exports.bech32 = genBech32('bech32');
exports.bech32m = genBech32('bech32m');
/**
 * UTF-8-to-byte decoder. Uses built-in TextDecoder / TextEncoder.
 * @example
 * const b = utf8.decode("hey"); // => new Uint8Array([ 104, 101, 121 ])
 * const str = utf8.encode(b); // "hey"
 */
exports.utf8 = {
    encode: (data) => new TextDecoder().decode(data),
    decode: (str) => new TextEncoder().encode(str),
};
/**
 * hex string decoder.
 * @example
 * const b = hex.decode("0102ff"); // => new Uint8Array([ 1, 2, 255 ])
 * const str = hex.encode(b); // "0102ff"
 */
exports.hex = chain(radix2(4), alphabet('0123456789abcdef'), join(''), normalize((s) => {
    if (typeof s !== 'string' || s.length % 2 !== 0)
        throw new TypeError(`hex.decode: expected string, got ${typeof s} with length ${s.length}`);
    return s.toLowerCase();
}));
// prettier-ignore
const CODERS = {
    utf8: exports.utf8, hex: exports.hex, base16: exports.base16, base32: exports.base32, base64: exports.base64, base64url: exports.base64url, base58: exports.base58, base58xmr: exports.base58xmr
};
const coderTypeError = 'Invalid encoding type. Available types: utf8, hex, base16, base32, base64, base64url, base58, base58xmr';
const bytesToString = (type, bytes) => {
    if (typeof type !== 'string' || !CODERS.hasOwnProperty(type))
        throw new TypeError(coderTypeError);
    if (!isBytes(bytes))
        throw new TypeError('bytesToString() expects Uint8Array');
    return CODERS[type].encode(bytes);
};
exports.bytesToString = bytesToString;
exports.str = exports.bytesToString; // as in python, but for bytes only
const stringToBytes = (type, str) => {
    if (!CODERS.hasOwnProperty(type))
        throw new TypeError(coderTypeError);
    if (typeof str !== 'string')
        throw new TypeError('stringToBytes() expects string');
    return CODERS[type].decode(str);
};
exports.stringToBytes = stringToBytes;
exports.bytes = exports.stringToBytes;


/***/ }),

/***/ "../../node_modules/@scure/bip32/lib/index.js":
/*!****************************************************!*\
  !*** ../../node_modules/@scure/bip32/lib/index.js ***!
  \****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.HDKey = exports.HARDENED_OFFSET = void 0;
/*! scure-bip32 - MIT License (c) 2022 Patricio Palladino, Paul Miller (paulmillr.com) */
const hmac_1 = __webpack_require__(/*! @noble/hashes/hmac */ "../../node_modules/@noble/hashes/hmac.js");
const ripemd160_1 = __webpack_require__(/*! @noble/hashes/ripemd160 */ "../../node_modules/@noble/hashes/ripemd160.js");
const sha256_1 = __webpack_require__(/*! @noble/hashes/sha256 */ "../../node_modules/@noble/hashes/sha256.js");
const sha512_1 = __webpack_require__(/*! @noble/hashes/sha512 */ "../../node_modules/@noble/hashes/sha512.js");
const _assert_1 = __webpack_require__(/*! @noble/hashes/_assert */ "../../node_modules/@noble/hashes/_assert.js");
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/hashes/utils.js");
const secp256k1_1 = __webpack_require__(/*! @noble/curves/secp256k1 */ "../../node_modules/@noble/curves/secp256k1.js");
const modular_1 = __webpack_require__(/*! @noble/curves/abstract/modular */ "../../node_modules/@noble/curves/abstract/modular.js");
const base_1 = __webpack_require__(/*! @scure/base */ "../../node_modules/@scure/base/lib/index.js");
const Point = secp256k1_1.secp256k1.ProjectivePoint;
const base58check = (0, base_1.createBase58check)(sha256_1.sha256);
function bytesToNumber(bytes) {
    (0, _assert_1.abytes)(bytes);
    const h = bytes.length === 0 ? '0' : (0, utils_1.bytesToHex)(bytes);
    return BigInt('0x' + h);
}
function numberToBytes(num) {
    if (typeof num !== 'bigint')
        throw new Error('bigint expected');
    return (0, utils_1.hexToBytes)(num.toString(16).padStart(64, '0'));
}
const MASTER_SECRET = (0, utils_1.utf8ToBytes)('Bitcoin seed');
// Bitcoin hardcoded by default
const BITCOIN_VERSIONS = { private: 0x0488ade4, public: 0x0488b21e };
exports.HARDENED_OFFSET = 0x80000000;
const hash160 = (data) => (0, ripemd160_1.ripemd160)((0, sha256_1.sha256)(data));
const fromU32 = (data) => (0, utils_1.createView)(data).getUint32(0, false);
const toU32 = (n) => {
    if (!Number.isSafeInteger(n) || n < 0 || n > 2 ** 32 - 1) {
        throw new Error('invalid number, should be from 0 to 2**32-1, got ' + n);
    }
    const buf = new Uint8Array(4);
    (0, utils_1.createView)(buf).setUint32(0, n, false);
    return buf;
};
class HDKey {
    get fingerprint() {
        if (!this.pubHash) {
            throw new Error('No publicKey set!');
        }
        return fromU32(this.pubHash);
    }
    get identifier() {
        return this.pubHash;
    }
    get pubKeyHash() {
        return this.pubHash;
    }
    get privateKey() {
        return this.privKeyBytes || null;
    }
    get publicKey() {
        return this.pubKey || null;
    }
    get privateExtendedKey() {
        const priv = this.privateKey;
        if (!priv) {
            throw new Error('No private key');
        }
        return base58check.encode(this.serialize(this.versions.private, (0, utils_1.concatBytes)(new Uint8Array([0]), priv)));
    }
    get publicExtendedKey() {
        if (!this.pubKey) {
            throw new Error('No public key');
        }
        return base58check.encode(this.serialize(this.versions.public, this.pubKey));
    }
    static fromMasterSeed(seed, versions = BITCOIN_VERSIONS) {
        (0, _assert_1.abytes)(seed);
        if (8 * seed.length < 128 || 8 * seed.length > 512) {
            throw new Error('HDKey: seed length must be between 128 and 512 bits; 256 bits is advised, got ' +
                seed.length);
        }
        const I = (0, hmac_1.hmac)(sha512_1.sha512, MASTER_SECRET, seed);
        return new HDKey({
            versions,
            chainCode: I.slice(32),
            privateKey: I.slice(0, 32),
        });
    }
    static fromExtendedKey(base58key, versions = BITCOIN_VERSIONS) {
        // => version(4) || depth(1) || fingerprint(4) || index(4) || chain(32) || key(33)
        const keyBuffer = base58check.decode(base58key);
        const keyView = (0, utils_1.createView)(keyBuffer);
        const version = keyView.getUint32(0, false);
        const opt = {
            versions,
            depth: keyBuffer[4],
            parentFingerprint: keyView.getUint32(5, false),
            index: keyView.getUint32(9, false),
            chainCode: keyBuffer.slice(13, 45),
        };
        const key = keyBuffer.slice(45);
        const isPriv = key[0] === 0;
        if (version !== versions[isPriv ? 'private' : 'public']) {
            throw new Error('Version mismatch');
        }
        if (isPriv) {
            return new HDKey({ ...opt, privateKey: key.slice(1) });
        }
        else {
            return new HDKey({ ...opt, publicKey: key });
        }
    }
    static fromJSON(json) {
        return HDKey.fromExtendedKey(json.xpriv);
    }
    constructor(opt) {
        this.depth = 0;
        this.index = 0;
        this.chainCode = null;
        this.parentFingerprint = 0;
        if (!opt || typeof opt !== 'object') {
            throw new Error('HDKey.constructor must not be called directly');
        }
        this.versions = opt.versions || BITCOIN_VERSIONS;
        this.depth = opt.depth || 0;
        this.chainCode = opt.chainCode || null;
        this.index = opt.index || 0;
        this.parentFingerprint = opt.parentFingerprint || 0;
        if (!this.depth) {
            if (this.parentFingerprint || this.index) {
                throw new Error('HDKey: zero depth with non-zero index/parent fingerprint');
            }
        }
        if (opt.publicKey && opt.privateKey) {
            throw new Error('HDKey: publicKey and privateKey at same time.');
        }
        if (opt.privateKey) {
            if (!secp256k1_1.secp256k1.utils.isValidPrivateKey(opt.privateKey)) {
                throw new Error('Invalid private key');
            }
            this.privKey =
                typeof opt.privateKey === 'bigint' ? opt.privateKey : bytesToNumber(opt.privateKey);
            this.privKeyBytes = numberToBytes(this.privKey);
            this.pubKey = secp256k1_1.secp256k1.getPublicKey(opt.privateKey, true);
        }
        else if (opt.publicKey) {
            this.pubKey = Point.fromHex(opt.publicKey).toRawBytes(true); // force compressed point
        }
        else {
            throw new Error('HDKey: no public or private key provided');
        }
        this.pubHash = hash160(this.pubKey);
    }
    derive(path) {
        if (!/^[mM]'?/.test(path)) {
            throw new Error('Path must start with "m" or "M"');
        }
        if (/^[mM]'?$/.test(path)) {
            return this;
        }
        const parts = path.replace(/^[mM]'?\//, '').split('/');
        // tslint:disable-next-line
        let child = this;
        for (const c of parts) {
            const m = /^(\d+)('?)$/.exec(c);
            const m1 = m && m[1];
            if (!m || m.length !== 3 || typeof m1 !== 'string')
                throw new Error('invalid child index: ' + c);
            let idx = +m1;
            if (!Number.isSafeInteger(idx) || idx >= exports.HARDENED_OFFSET) {
                throw new Error('Invalid index');
            }
            // hardened key
            if (m[2] === "'") {
                idx += exports.HARDENED_OFFSET;
            }
            child = child.deriveChild(idx);
        }
        return child;
    }
    deriveChild(index) {
        if (!this.pubKey || !this.chainCode) {
            throw new Error('No publicKey or chainCode set');
        }
        let data = toU32(index);
        if (index >= exports.HARDENED_OFFSET) {
            // Hardened
            const priv = this.privateKey;
            if (!priv) {
                throw new Error('Could not derive hardened child key');
            }
            // Hardened child: 0x00 || ser256(kpar) || ser32(index)
            data = (0, utils_1.concatBytes)(new Uint8Array([0]), priv, data);
        }
        else {
            // Normal child: serP(point(kpar)) || ser32(index)
            data = (0, utils_1.concatBytes)(this.pubKey, data);
        }
        const I = (0, hmac_1.hmac)(sha512_1.sha512, this.chainCode, data);
        const childTweak = bytesToNumber(I.slice(0, 32));
        const chainCode = I.slice(32);
        if (!secp256k1_1.secp256k1.utils.isValidPrivateKey(childTweak)) {
            throw new Error('Tweak bigger than curve order');
        }
        const opt = {
            versions: this.versions,
            chainCode,
            depth: this.depth + 1,
            parentFingerprint: this.fingerprint,
            index,
        };
        try {
            // Private parent key -> private child key
            if (this.privateKey) {
                const added = (0, modular_1.mod)(this.privKey + childTweak, secp256k1_1.secp256k1.CURVE.n);
                if (!secp256k1_1.secp256k1.utils.isValidPrivateKey(added)) {
                    throw new Error('The tweak was out of range or the resulted private key is invalid');
                }
                opt.privateKey = added;
            }
            else {
                const added = Point.fromHex(this.pubKey).add(Point.fromPrivateKey(childTweak));
                // Cryptographically impossible: hmac-sha512 preimage would need to be found
                if (added.equals(Point.ZERO)) {
                    throw new Error('The tweak was equal to negative P, which made the result key invalid');
                }
                opt.publicKey = added.toRawBytes(true);
            }
            return new HDKey(opt);
        }
        catch (err) {
            return this.deriveChild(index + 1);
        }
    }
    sign(hash) {
        if (!this.privateKey) {
            throw new Error('No privateKey set!');
        }
        (0, _assert_1.abytes)(hash, 32);
        return secp256k1_1.secp256k1.sign(hash, this.privKey).toCompactRawBytes();
    }
    verify(hash, signature) {
        (0, _assert_1.abytes)(hash, 32);
        (0, _assert_1.abytes)(signature, 64);
        if (!this.publicKey) {
            throw new Error('No publicKey set!');
        }
        let sig;
        try {
            sig = secp256k1_1.secp256k1.Signature.fromCompact(signature);
        }
        catch (error) {
            return false;
        }
        return secp256k1_1.secp256k1.verify(sig, hash, this.publicKey);
    }
    wipePrivateData() {
        this.privKey = undefined;
        if (this.privKeyBytes) {
            this.privKeyBytes.fill(0);
            this.privKeyBytes = undefined;
        }
        return this;
    }
    toJSON() {
        return {
            xpriv: this.privateExtendedKey,
            xpub: this.publicExtendedKey,
        };
    }
    serialize(version, key) {
        if (!this.chainCode) {
            throw new Error('No chainCode set');
        }
        (0, _assert_1.abytes)(key, 33);
        // version(4) || depth(1) || fingerprint(4) || index(4) || chain(32) || key(33)
        return (0, utils_1.concatBytes)(toU32(version), new Uint8Array([this.depth]), toU32(this.parentFingerprint), toU32(this.index), this.chainCode, key);
    }
}
exports.HDKey = HDKey;


/***/ }),

/***/ "../../node_modules/@scure/bip39/index.js":
/*!************************************************!*\
  !*** ../../node_modules/@scure/bip39/index.js ***!
  \************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.generateMnemonic = generateMnemonic;
exports.mnemonicToEntropy = mnemonicToEntropy;
exports.entropyToMnemonic = entropyToMnemonic;
exports.validateMnemonic = validateMnemonic;
exports.mnemonicToSeed = mnemonicToSeed;
exports.mnemonicToSeedSync = mnemonicToSeedSync;
/*! scure-bip39 - MIT License (c) 2022 Patricio Palladino, Paul Miller (paulmillr.com) */
const _assert_1 = __webpack_require__(/*! @noble/hashes/_assert */ "../../node_modules/@noble/hashes/_assert.js");
const pbkdf2_1 = __webpack_require__(/*! @noble/hashes/pbkdf2 */ "../../node_modules/@noble/hashes/pbkdf2.js");
const sha256_1 = __webpack_require__(/*! @noble/hashes/sha256 */ "../../node_modules/@noble/hashes/sha256.js");
const sha512_1 = __webpack_require__(/*! @noble/hashes/sha512 */ "../../node_modules/@noble/hashes/sha512.js");
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/hashes/utils.js");
const base_1 = __webpack_require__(/*! @scure/base */ "../../node_modules/@scure/base/lib/index.js");
// Japanese wordlist
const isJapanese = (wordlist) => wordlist[0] === '\u3042\u3044\u3053\u304f\u3057\u3093';
// Normalization replaces equivalent sequences of characters
// so that any two texts that are equivalent will be reduced
// to the same sequence of code points, called the normal form of the original text.
// https://tonsky.me/blog/unicode/#why-is-a----
function nfkd(str) {
    if (typeof str !== 'string')
        throw new TypeError('invalid mnemonic type: ' + typeof str);
    return str.normalize('NFKD');
}
function normalize(str) {
    const norm = nfkd(str);
    const words = norm.split(' ');
    if (![12, 15, 18, 21, 24].includes(words.length))
        throw new Error('Invalid mnemonic');
    return { nfkd: norm, words };
}
function assertEntropy(entropy) {
    (0, _assert_1.abytes)(entropy, 16, 20, 24, 28, 32);
}
/**
 * Generate x random words. Uses Cryptographically-Secure Random Number Generator.
 * @param wordlist imported wordlist for specific language
 * @param strength mnemonic strength 128-256 bits
 * @example
 * generateMnemonic(wordlist, 128)
 * // 'legal winner thank year wave sausage worth useful legal winner thank yellow'
 */
function generateMnemonic(wordlist, strength = 128) {
    (0, _assert_1.anumber)(strength);
    if (strength % 32 !== 0 || strength > 256)
        throw new TypeError('Invalid entropy');
    return entropyToMnemonic((0, utils_1.randomBytes)(strength / 8), wordlist);
}
const calcChecksum = (entropy) => {
    // Checksum is ent.length/4 bits long
    const bitsLeft = 8 - entropy.length / 4;
    // Zero rightmost "bitsLeft" bits in byte
    // For example: bitsLeft=4 val=10111101 -> 10110000
    return new Uint8Array([((0, sha256_1.sha256)(entropy)[0] >> bitsLeft) << bitsLeft]);
};
function getCoder(wordlist) {
    if (!Array.isArray(wordlist) || wordlist.length !== 2048 || typeof wordlist[0] !== 'string')
        throw new Error('Wordlist: expected array of 2048 strings');
    wordlist.forEach((i) => {
        if (typeof i !== 'string')
            throw new Error('wordlist: non-string element: ' + i);
    });
    return base_1.utils.chain(base_1.utils.checksum(1, calcChecksum), base_1.utils.radix2(11, true), base_1.utils.alphabet(wordlist));
}
/**
 * Reversible: Converts mnemonic string to raw entropy in form of byte array.
 * @param mnemonic 12-24 words
 * @param wordlist imported wordlist for specific language
 * @example
 * const mnem = 'legal winner thank year wave sausage worth useful legal winner thank yellow';
 * mnemonicToEntropy(mnem, wordlist)
 * // Produces
 * new Uint8Array([
 *   0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f,
 *   0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f
 * ])
 */
function mnemonicToEntropy(mnemonic, wordlist) {
    const { words } = normalize(mnemonic);
    const entropy = getCoder(wordlist).decode(words);
    assertEntropy(entropy);
    return entropy;
}
/**
 * Reversible: Converts raw entropy in form of byte array to mnemonic string.
 * @param entropy byte array
 * @param wordlist imported wordlist for specific language
 * @returns 12-24 words
 * @example
 * const ent = new Uint8Array([
 *   0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f,
 *   0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f, 0x7f
 * ]);
 * entropyToMnemonic(ent, wordlist);
 * // 'legal winner thank year wave sausage worth useful legal winner thank yellow'
 */
function entropyToMnemonic(entropy, wordlist) {
    assertEntropy(entropy);
    const words = getCoder(wordlist).encode(entropy);
    return words.join(isJapanese(wordlist) ? '\u3000' : ' ');
}
/**
 * Validates mnemonic for being 12-24 words contained in `wordlist`.
 */
function validateMnemonic(mnemonic, wordlist) {
    try {
        mnemonicToEntropy(mnemonic, wordlist);
    }
    catch (e) {
        return false;
    }
    return true;
}
const salt = (passphrase) => nfkd('mnemonic' + passphrase);
/**
 * Irreversible: Uses KDF to derive 64 bytes of key data from mnemonic + optional password.
 * @param mnemonic 12-24 words
 * @param passphrase string that will additionally protect the key
 * @returns 64 bytes of key data
 * @example
 * const mnem = 'legal winner thank year wave sausage worth useful legal winner thank yellow';
 * await mnemonicToSeed(mnem, 'password');
 * // new Uint8Array([...64 bytes])
 */
function mnemonicToSeed(mnemonic, passphrase = '') {
    return (0, pbkdf2_1.pbkdf2Async)(sha512_1.sha512, normalize(mnemonic).nfkd, salt(passphrase), { c: 2048, dkLen: 64 });
}
/**
 * Irreversible: Uses KDF to derive 64 bytes of key data from mnemonic + optional password.
 * @param mnemonic 12-24 words
 * @param passphrase string that will additionally protect the key
 * @returns 64 bytes of key data
 * @example
 * const mnem = 'legal winner thank year wave sausage worth useful legal winner thank yellow';
 * mnemonicToSeedSync(mnem, 'password');
 * // new Uint8Array([...64 bytes])
 */
function mnemonicToSeedSync(mnemonic, passphrase = '') {
    return (0, pbkdf2_1.pbkdf2)(sha512_1.sha512, normalize(mnemonic).nfkd, salt(passphrase), { c: 2048, dkLen: 64 });
}


/***/ }),

/***/ "../../node_modules/@scure/bip39/wordlists/english.js":
/*!************************************************************!*\
  !*** ../../node_modules/@scure/bip39/wordlists/english.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.wordlist = void 0;
exports.wordlist = `abandon
ability
able
about
above
absent
absorb
abstract
absurd
abuse
access
accident
account
accuse
achieve
acid
acoustic
acquire
across
act
action
actor
actress
actual
adapt
add
addict
address
adjust
admit
adult
advance
advice
aerobic
affair
afford
afraid
again
age
agent
agree
ahead
aim
air
airport
aisle
alarm
album
alcohol
alert
alien
all
alley
allow
almost
alone
alpha
already
also
alter
always
amateur
amazing
among
amount
amused
analyst
anchor
ancient
anger
angle
angry
animal
ankle
announce
annual
another
answer
antenna
antique
anxiety
any
apart
apology
appear
apple
approve
april
arch
arctic
area
arena
argue
arm
armed
armor
army
around
arrange
arrest
arrive
arrow
art
artefact
artist
artwork
ask
aspect
assault
asset
assist
assume
asthma
athlete
atom
attack
attend
attitude
attract
auction
audit
august
aunt
author
auto
autumn
average
avocado
avoid
awake
aware
away
awesome
awful
awkward
axis
baby
bachelor
bacon
badge
bag
balance
balcony
ball
bamboo
banana
banner
bar
barely
bargain
barrel
base
basic
basket
battle
beach
bean
beauty
because
become
beef
before
begin
behave
behind
believe
below
belt
bench
benefit
best
betray
better
between
beyond
bicycle
bid
bike
bind
biology
bird
birth
bitter
black
blade
blame
blanket
blast
bleak
bless
blind
blood
blossom
blouse
blue
blur
blush
board
boat
body
boil
bomb
bone
bonus
book
boost
border
boring
borrow
boss
bottom
bounce
box
boy
bracket
brain
brand
brass
brave
bread
breeze
brick
bridge
brief
bright
bring
brisk
broccoli
broken
bronze
broom
brother
brown
brush
bubble
buddy
budget
buffalo
build
bulb
bulk
bullet
bundle
bunker
burden
burger
burst
bus
business
busy
butter
buyer
buzz
cabbage
cabin
cable
cactus
cage
cake
call
calm
camera
camp
can
canal
cancel
candy
cannon
canoe
canvas
canyon
capable
capital
captain
car
carbon
card
cargo
carpet
carry
cart
case
cash
casino
castle
casual
cat
catalog
catch
category
cattle
caught
cause
caution
cave
ceiling
celery
cement
census
century
cereal
certain
chair
chalk
champion
change
chaos
chapter
charge
chase
chat
cheap
check
cheese
chef
cherry
chest
chicken
chief
child
chimney
choice
choose
chronic
chuckle
chunk
churn
cigar
cinnamon
circle
citizen
city
civil
claim
clap
clarify
claw
clay
clean
clerk
clever
click
client
cliff
climb
clinic
clip
clock
clog
close
cloth
cloud
clown
club
clump
cluster
clutch
coach
coast
coconut
code
coffee
coil
coin
collect
color
column
combine
come
comfort
comic
common
company
concert
conduct
confirm
congress
connect
consider
control
convince
cook
cool
copper
copy
coral
core
corn
correct
cost
cotton
couch
country
couple
course
cousin
cover
coyote
crack
cradle
craft
cram
crane
crash
crater
crawl
crazy
cream
credit
creek
crew
cricket
crime
crisp
critic
crop
cross
crouch
crowd
crucial
cruel
cruise
crumble
crunch
crush
cry
crystal
cube
culture
cup
cupboard
curious
current
curtain
curve
cushion
custom
cute
cycle
dad
damage
damp
dance
danger
daring
dash
daughter
dawn
day
deal
debate
debris
decade
december
decide
decline
decorate
decrease
deer
defense
define
defy
degree
delay
deliver
demand
demise
denial
dentist
deny
depart
depend
deposit
depth
deputy
derive
describe
desert
design
desk
despair
destroy
detail
detect
develop
device
devote
diagram
dial
diamond
diary
dice
diesel
diet
differ
digital
dignity
dilemma
dinner
dinosaur
direct
dirt
disagree
discover
disease
dish
dismiss
disorder
display
distance
divert
divide
divorce
dizzy
doctor
document
dog
doll
dolphin
domain
donate
donkey
donor
door
dose
double
dove
draft
dragon
drama
drastic
draw
dream
dress
drift
drill
drink
drip
drive
drop
drum
dry
duck
dumb
dune
during
dust
dutch
duty
dwarf
dynamic
eager
eagle
early
earn
earth
easily
east
easy
echo
ecology
economy
edge
edit
educate
effort
egg
eight
either
elbow
elder
electric
elegant
element
elephant
elevator
elite
else
embark
embody
embrace
emerge
emotion
employ
empower
empty
enable
enact
end
endless
endorse
enemy
energy
enforce
engage
engine
enhance
enjoy
enlist
enough
enrich
enroll
ensure
enter
entire
entry
envelope
episode
equal
equip
era
erase
erode
erosion
error
erupt
escape
essay
essence
estate
eternal
ethics
evidence
evil
evoke
evolve
exact
example
excess
exchange
excite
exclude
excuse
execute
exercise
exhaust
exhibit
exile
exist
exit
exotic
expand
expect
expire
explain
expose
express
extend
extra
eye
eyebrow
fabric
face
faculty
fade
faint
faith
fall
false
fame
family
famous
fan
fancy
fantasy
farm
fashion
fat
fatal
father
fatigue
fault
favorite
feature
february
federal
fee
feed
feel
female
fence
festival
fetch
fever
few
fiber
fiction
field
figure
file
film
filter
final
find
fine
finger
finish
fire
firm
first
fiscal
fish
fit
fitness
fix
flag
flame
flash
flat
flavor
flee
flight
flip
float
flock
floor
flower
fluid
flush
fly
foam
focus
fog
foil
fold
follow
food
foot
force
forest
forget
fork
fortune
forum
forward
fossil
foster
found
fox
fragile
frame
frequent
fresh
friend
fringe
frog
front
frost
frown
frozen
fruit
fuel
fun
funny
furnace
fury
future
gadget
gain
galaxy
gallery
game
gap
garage
garbage
garden
garlic
garment
gas
gasp
gate
gather
gauge
gaze
general
genius
genre
gentle
genuine
gesture
ghost
giant
gift
giggle
ginger
giraffe
girl
give
glad
glance
glare
glass
glide
glimpse
globe
gloom
glory
glove
glow
glue
goat
goddess
gold
good
goose
gorilla
gospel
gossip
govern
gown
grab
grace
grain
grant
grape
grass
gravity
great
green
grid
grief
grit
grocery
group
grow
grunt
guard
guess
guide
guilt
guitar
gun
gym
habit
hair
half
hammer
hamster
hand
happy
harbor
hard
harsh
harvest
hat
have
hawk
hazard
head
health
heart
heavy
hedgehog
height
hello
helmet
help
hen
hero
hidden
high
hill
hint
hip
hire
history
hobby
hockey
hold
hole
holiday
hollow
home
honey
hood
hope
horn
horror
horse
hospital
host
hotel
hour
hover
hub
huge
human
humble
humor
hundred
hungry
hunt
hurdle
hurry
hurt
husband
hybrid
ice
icon
idea
identify
idle
ignore
ill
illegal
illness
image
imitate
immense
immune
impact
impose
improve
impulse
inch
include
income
increase
index
indicate
indoor
industry
infant
inflict
inform
inhale
inherit
initial
inject
injury
inmate
inner
innocent
input
inquiry
insane
insect
inside
inspire
install
intact
interest
into
invest
invite
involve
iron
island
isolate
issue
item
ivory
jacket
jaguar
jar
jazz
jealous
jeans
jelly
jewel
job
join
joke
journey
joy
judge
juice
jump
jungle
junior
junk
just
kangaroo
keen
keep
ketchup
key
kick
kid
kidney
kind
kingdom
kiss
kit
kitchen
kite
kitten
kiwi
knee
knife
knock
know
lab
label
labor
ladder
lady
lake
lamp
language
laptop
large
later
latin
laugh
laundry
lava
law
lawn
lawsuit
layer
lazy
leader
leaf
learn
leave
lecture
left
leg
legal
legend
leisure
lemon
lend
length
lens
leopard
lesson
letter
level
liar
liberty
library
license
life
lift
light
like
limb
limit
link
lion
liquid
list
little
live
lizard
load
loan
lobster
local
lock
logic
lonely
long
loop
lottery
loud
lounge
love
loyal
lucky
luggage
lumber
lunar
lunch
luxury
lyrics
machine
mad
magic
magnet
maid
mail
main
major
make
mammal
man
manage
mandate
mango
mansion
manual
maple
marble
march
margin
marine
market
marriage
mask
mass
master
match
material
math
matrix
matter
maximum
maze
meadow
mean
measure
meat
mechanic
medal
media
melody
melt
member
memory
mention
menu
mercy
merge
merit
merry
mesh
message
metal
method
middle
midnight
milk
million
mimic
mind
minimum
minor
minute
miracle
mirror
misery
miss
mistake
mix
mixed
mixture
mobile
model
modify
mom
moment
monitor
monkey
monster
month
moon
moral
more
morning
mosquito
mother
motion
motor
mountain
mouse
move
movie
much
muffin
mule
multiply
muscle
museum
mushroom
music
must
mutual
myself
mystery
myth
naive
name
napkin
narrow
nasty
nation
nature
near
neck
need
negative
neglect
neither
nephew
nerve
nest
net
network
neutral
never
news
next
nice
night
noble
noise
nominee
noodle
normal
north
nose
notable
note
nothing
notice
novel
now
nuclear
number
nurse
nut
oak
obey
object
oblige
obscure
observe
obtain
obvious
occur
ocean
october
odor
off
offer
office
often
oil
okay
old
olive
olympic
omit
once
one
onion
online
only
open
opera
opinion
oppose
option
orange
orbit
orchard
order
ordinary
organ
orient
original
orphan
ostrich
other
outdoor
outer
output
outside
oval
oven
over
own
owner
oxygen
oyster
ozone
pact
paddle
page
pair
palace
palm
panda
panel
panic
panther
paper
parade
parent
park
parrot
party
pass
patch
path
patient
patrol
pattern
pause
pave
payment
peace
peanut
pear
peasant
pelican
pen
penalty
pencil
people
pepper
perfect
permit
person
pet
phone
photo
phrase
physical
piano
picnic
picture
piece
pig
pigeon
pill
pilot
pink
pioneer
pipe
pistol
pitch
pizza
place
planet
plastic
plate
play
please
pledge
pluck
plug
plunge
poem
poet
point
polar
pole
police
pond
pony
pool
popular
portion
position
possible
post
potato
pottery
poverty
powder
power
practice
praise
predict
prefer
prepare
present
pretty
prevent
price
pride
primary
print
priority
prison
private
prize
problem
process
produce
profit
program
project
promote
proof
property
prosper
protect
proud
provide
public
pudding
pull
pulp
pulse
pumpkin
punch
pupil
puppy
purchase
purity
purpose
purse
push
put
puzzle
pyramid
quality
quantum
quarter
question
quick
quit
quiz
quote
rabbit
raccoon
race
rack
radar
radio
rail
rain
raise
rally
ramp
ranch
random
range
rapid
rare
rate
rather
raven
raw
razor
ready
real
reason
rebel
rebuild
recall
receive
recipe
record
recycle
reduce
reflect
reform
refuse
region
regret
regular
reject
relax
release
relief
rely
remain
remember
remind
remove
render
renew
rent
reopen
repair
repeat
replace
report
require
rescue
resemble
resist
resource
response
result
retire
retreat
return
reunion
reveal
review
reward
rhythm
rib
ribbon
rice
rich
ride
ridge
rifle
right
rigid
ring
riot
ripple
risk
ritual
rival
river
road
roast
robot
robust
rocket
romance
roof
rookie
room
rose
rotate
rough
round
route
royal
rubber
rude
rug
rule
run
runway
rural
sad
saddle
sadness
safe
sail
salad
salmon
salon
salt
salute
same
sample
sand
satisfy
satoshi
sauce
sausage
save
say
scale
scan
scare
scatter
scene
scheme
school
science
scissors
scorpion
scout
scrap
screen
script
scrub
sea
search
season
seat
second
secret
section
security
seed
seek
segment
select
sell
seminar
senior
sense
sentence
series
service
session
settle
setup
seven
shadow
shaft
shallow
share
shed
shell
sheriff
shield
shift
shine
ship
shiver
shock
shoe
shoot
shop
short
shoulder
shove
shrimp
shrug
shuffle
shy
sibling
sick
side
siege
sight
sign
silent
silk
silly
silver
similar
simple
since
sing
siren
sister
situate
six
size
skate
sketch
ski
skill
skin
skirt
skull
slab
slam
sleep
slender
slice
slide
slight
slim
slogan
slot
slow
slush
small
smart
smile
smoke
smooth
snack
snake
snap
sniff
snow
soap
soccer
social
sock
soda
soft
solar
soldier
solid
solution
solve
someone
song
soon
sorry
sort
soul
sound
soup
source
south
space
spare
spatial
spawn
speak
special
speed
spell
spend
sphere
spice
spider
spike
spin
spirit
split
spoil
sponsor
spoon
sport
spot
spray
spread
spring
spy
square
squeeze
squirrel
stable
stadium
staff
stage
stairs
stamp
stand
start
state
stay
steak
steel
stem
step
stereo
stick
still
sting
stock
stomach
stone
stool
story
stove
strategy
street
strike
strong
struggle
student
stuff
stumble
style
subject
submit
subway
success
such
sudden
suffer
sugar
suggest
suit
summer
sun
sunny
sunset
super
supply
supreme
sure
surface
surge
surprise
surround
survey
suspect
sustain
swallow
swamp
swap
swarm
swear
sweet
swift
swim
swing
switch
sword
symbol
symptom
syrup
system
table
tackle
tag
tail
talent
talk
tank
tape
target
task
taste
tattoo
taxi
teach
team
tell
ten
tenant
tennis
tent
term
test
text
thank
that
theme
then
theory
there
they
thing
this
thought
three
thrive
throw
thumb
thunder
ticket
tide
tiger
tilt
timber
time
tiny
tip
tired
tissue
title
toast
tobacco
today
toddler
toe
together
toilet
token
tomato
tomorrow
tone
tongue
tonight
tool
tooth
top
topic
topple
torch
tornado
tortoise
toss
total
tourist
toward
tower
town
toy
track
trade
traffic
tragic
train
transfer
trap
trash
travel
tray
treat
tree
trend
trial
tribe
trick
trigger
trim
trip
trophy
trouble
truck
true
truly
trumpet
trust
truth
try
tube
tuition
tumble
tuna
tunnel
turkey
turn
turtle
twelve
twenty
twice
twin
twist
two
type
typical
ugly
umbrella
unable
unaware
uncle
uncover
under
undo
unfair
unfold
unhappy
uniform
unique
unit
universe
unknown
unlock
until
unusual
unveil
update
upgrade
uphold
upon
upper
upset
urban
urge
usage
use
used
useful
useless
usual
utility
vacant
vacuum
vague
valid
valley
valve
van
vanish
vapor
various
vast
vault
vehicle
velvet
vendor
venture
venue
verb
verify
version
very
vessel
veteran
viable
vibrant
vicious
victory
video
view
village
vintage
violin
virtual
virus
visa
visit
visual
vital
vivid
vocal
voice
void
volcano
volume
vote
voyage
wage
wagon
wait
walk
wall
walnut
want
warfare
warm
warrior
wash
wasp
waste
water
wave
way
wealth
weapon
wear
weasel
weather
web
wedding
weekend
weird
welcome
west
wet
whale
what
wheat
wheel
when
where
whip
whisper
wide
width
wife
wild
will
win
window
wine
wing
wink
winner
winter
wire
wisdom
wise
wish
witness
wolf
woman
wonder
wood
wool
word
work
world
worry
worth
wrap
wreck
wrestle
wrist
write
wrong
yard
year
yellow
you
young
youth
zebra
zero
zone
zoo`.split('\n');


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/internal/normalizeInput.js":
/*!****************************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/internal/normalizeInput.js ***!
  \****************************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
/**
 * Normalize a string, number array, or Uint8Array to a string or Uint8Array.
 * Both node and noble lib functions accept these types.
 *
 * @param input - value to normalize
 */
function normalizeInput(input) {
    return Array.isArray(input) ? new Uint8Array(input) : input;
}
exports["default"] = normalizeInput;


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/internal/wrapNoble.js":
/*!***********************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/internal/wrapNoble.js ***!
  \***********************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const normalizeInput_1 = __importDefault(__webpack_require__(/*! ./normalizeInput */ "../../node_modules/@xrplf/isomorphic/dist/internal/normalizeInput.js"));
/**
 * Wrap a CHash object from @noble/hashes to provide a interface that is isomorphic
 *
 * @param chash - {CHash} hash function to wrap
 */
function wrapNoble(chash) {
    function wrapped(input) {
        return chash((0, normalizeInput_1.default)(input));
    }
    wrapped.create = () => {
        const hash = chash.create();
        return {
            update(input) {
                hash.update((0, normalizeInput_1.default)(input));
                return this;
            },
            digest() {
                return hash.digest();
            },
        };
    };
    return wrapped;
}
exports["default"] = wrapNoble;


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/ripemd160/browser.js":
/*!**********************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/ripemd160/browser.js ***!
  \**********************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.ripemd160 = void 0;
const ripemd160_1 = __webpack_require__(/*! @noble/hashes/ripemd160 */ "../../node_modules/@noble/hashes/ripemd160.js");
const wrapNoble_1 = __importDefault(__webpack_require__(/*! ../internal/wrapNoble */ "../../node_modules/@xrplf/isomorphic/dist/internal/wrapNoble.js"));
/**
 * Wrap noble-libs's ripemd160 implementation in HashFn
 */
exports.ripemd160 = (0, wrapNoble_1.default)(ripemd160_1.ripemd160);


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/sha256/browser.js":
/*!*******************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/sha256/browser.js ***!
  \*******************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sha256 = void 0;
const sha256_1 = __webpack_require__(/*! @noble/hashes/sha256 */ "../../node_modules/@noble/hashes/sha256.js");
const wrapNoble_1 = __importDefault(__webpack_require__(/*! ../internal/wrapNoble */ "../../node_modules/@xrplf/isomorphic/dist/internal/wrapNoble.js"));
/**
 * Wrap noble-libs's sha256 implementation in HashFn
 */
exports.sha256 = (0, wrapNoble_1.default)(sha256_1.sha256);


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/sha512/browser.js":
/*!*******************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/sha512/browser.js ***!
  \*******************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sha512 = void 0;
const sha512_1 = __webpack_require__(/*! @noble/hashes/sha512 */ "../../node_modules/@noble/hashes/sha512.js");
const wrapNoble_1 = __importDefault(__webpack_require__(/*! ../internal/wrapNoble */ "../../node_modules/@xrplf/isomorphic/dist/internal/wrapNoble.js"));
/**
 * Wrap noble-libs's sha512 implementation in HashFn
 */
exports.sha512 = (0, wrapNoble_1.default)(sha512_1.sha512);


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js":
/*!******************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/utils/browser.js ***!
  \******************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.randomBytes = exports.stringToHex = exports.hexToString = exports.hexToBytes = exports.bytesToHex = void 0;
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/hashes/utils.js");
const shared_1 = __webpack_require__(/*! ./shared */ "../../node_modules/@xrplf/isomorphic/dist/utils/shared.js");
/* eslint-disable func-style -- Typed to ensure uniformity between node and browser implementations and docs */
const bytesToHex = (bytes) => {
    const hex = (0, utils_1.bytesToHex)(bytes instanceof Uint8Array ? bytes : Uint8Array.from(bytes));
    return hex.toUpperCase();
};
exports.bytesToHex = bytesToHex;
// A clone of hexToBytes from @noble/hashes without the length checks. This allows us to do our own checks.
const hexToBytes = (hex) => {
    const len = hex.length;
    const array = new Uint8Array(len / 2);
    if (!shared_1.HEX_REGEX.test(hex)) {
        throw new Error('Invalid hex string');
    }
    for (let i = 0; i < array.length; i++) {
        const j = i * 2;
        const hexByte = hex.slice(j, j + 2);
        const byte = Number.parseInt(hexByte, 16);
        if (Number.isNaN(byte) || byte < 0) {
            throw new Error('Invalid byte sequence');
        }
        array[i] = byte;
    }
    return array;
};
exports.hexToBytes = hexToBytes;
const hexToString = (hex, encoding = 'utf8') => {
    return new TextDecoder(encoding).decode((0, exports.hexToBytes)(hex));
};
exports.hexToString = hexToString;
const stringToHex = (string) => {
    return (0, exports.bytesToHex)(new TextEncoder().encode(string));
};
exports.stringToHex = stringToHex;
/* eslint-enable func-style */
exports.randomBytes = utils_1.randomBytes;
__exportStar(__webpack_require__(/*! ./shared */ "../../node_modules/@xrplf/isomorphic/dist/utils/shared.js"), exports);


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/utils/shared.js":
/*!*****************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/utils/shared.js ***!
  \*****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.equal = exports.concat = exports.HEX_REGEX = void 0;
const utils_1 = __webpack_require__(/*! @noble/hashes/utils */ "../../node_modules/@noble/hashes/utils.js");
exports.HEX_REGEX = /^[A-F0-9]*$/iu;
function concat(views) {
    return (0, utils_1.concatBytes)(...views);
}
exports.concat = concat;
function equal(buf1, buf2) {
    if (buf1.byteLength !== buf2.byteLength) {
        return false;
    }
    const dv1 = new Int8Array(buf1);
    const dv2 = new Int8Array(buf2);
    for (let i = 0; i !== buf1.byteLength; i++) {
        if (dv1[i] !== dv2[i]) {
            return false;
        }
    }
    return true;
}
exports.equal = equal;


/***/ }),

/***/ "../../node_modules/@xrplf/isomorphic/dist/ws/browser.js":
/*!***************************************************************!*\
  !*** ../../node_modules/@xrplf/isomorphic/dist/ws/browser.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
/* eslint-disable max-classes-per-file -- Needs to be a wrapper for ws */
const eventemitter3_1 = __webpack_require__(/*! eventemitter3 */ "../../node_modules/eventemitter3/index.js");
/**
 * Provides `EventEmitter` interface for native browser `WebSocket`,
 * same, as `ws` package provides.
 */
class WSWrapper extends eventemitter3_1.EventEmitter {
    /**
     * Constructs a browser-safe websocket.
     *
     * @param url - URL to connect to.
     * @param _protocols - Not used.
     * @param _websocketOptions - Not used.
     */
    constructor(url, _protocols, _websocketOptions) {
        super();
        this.ws = new WebSocket(url);
        this.ws.onclose = (closeEvent) => {
            let reason;
            if (closeEvent.reason) {
                const enc = new TextEncoder();
                reason = enc.encode(closeEvent.reason);
            }
            this.emit('close', closeEvent.code, reason);
        };
        this.ws.onopen = () => {
            this.emit('open');
        };
        this.ws.onerror = (error) => {
            this.emit('error', error);
        };
        this.ws.onmessage = (message) => {
            this.emit('message', message.data);
        };
    }
    /**
     * Get the ready state of the websocket.
     *
     * @returns The Websocket's ready state.
     */
    get readyState() {
        return this.ws.readyState;
    }
    /**
     * Closes the websocket.
     *
     * @param code - Close code.
     * @param reason - Close reason.
     */
    close(code, reason) {
        if (this.readyState === 1) {
            this.ws.close(code, reason);
        }
    }
    /**
     * Sends a message over the Websocket connection.
     *
     * @param message - Message to send.
     */
    send(message) {
        this.ws.send(message);
    }
}
WSWrapper.CONNECTING = 0;
WSWrapper.OPEN = 1;
WSWrapper.CLOSING = 2;
WSWrapper.CLOSED = 3;
exports["default"] = WSWrapper;


/***/ }),

/***/ "../../node_modules/@xrplf/secret-numbers/dist/index.js":
/*!**************************************************************!*\
  !*** ../../node_modules/@xrplf/secret-numbers/dist/index.js ***!
  \**************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
__exportStar(__webpack_require__(/*! ./schema/Account */ "../../node_modules/@xrplf/secret-numbers/dist/schema/Account.js"), exports);
__exportStar(__webpack_require__(/*! ./utils */ "../../node_modules/@xrplf/secret-numbers/dist/utils/index.js"), exports);


/***/ }),

/***/ "../../node_modules/@xrplf/secret-numbers/dist/schema/Account.js":
/*!***********************************************************************!*\
  !*** ../../node_modules/@xrplf/secret-numbers/dist/schema/Account.js ***!
  \***********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Account = void 0;
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
const utils_1 = __webpack_require__(/*! ../utils */ "../../node_modules/@xrplf/secret-numbers/dist/utils/index.js");
class Account {
    constructor(secretNumbers) {
        this._account = {
            familySeed: '',
            address: '',
            keypair: {
                publicKey: '',
                privateKey: '',
            },
        };
        if (typeof secretNumbers === 'string') {
            this._secret = (0, utils_1.parseSecretString)(secretNumbers);
        }
        else if (Array.isArray(secretNumbers)) {
            this._secret = secretNumbers;
        }
        else if (secretNumbers instanceof Uint8Array) {
            this._secret = (0, utils_1.entropyToSecret)(secretNumbers);
        }
        else {
            this._secret = (0, utils_1.randomSecret)();
        }
        validateLengths(this._secret);
        this.derive();
    }
    getSecret() {
        return this._secret;
    }
    getSecretString() {
        return this._secret.join(' ');
    }
    getAddress() {
        return this._account.address;
    }
    getFamilySeed() {
        return this._account.familySeed;
    }
    getKeypair() {
        return this._account.keypair;
    }
    toString() {
        return this.getSecretString();
    }
    derive() {
        try {
            const entropy = (0, utils_1.secretToEntropy)(this._secret);
            this._account.familySeed = (0, ripple_keypairs_1.generateSeed)({ entropy });
            this._account.keypair = (0, ripple_keypairs_1.deriveKeypair)(this._account.familySeed);
            this._account.address = (0, ripple_keypairs_1.deriveAddress)(this._account.keypair.publicKey);
        }
        catch (error) {
            let message = 'Unknown Error';
            if (error instanceof Error) {
                message = error.message;
            }
            throw new Error(message);
        }
    }
}
exports.Account = Account;
function validateLengths(secretNumbers) {
    if (secretNumbers.length !== 8) {
        throw new Error('Secret must have 8 numbers');
    }
    secretNumbers.forEach((num) => {
        if (num.length !== 6) {
            throw new Error('Each secret number must be 6 digits');
        }
    });
}


/***/ }),

/***/ "../../node_modules/@xrplf/secret-numbers/dist/utils/index.js":
/*!********************************************************************!*\
  !*** ../../node_modules/@xrplf/secret-numbers/dist/utils/index.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.parseSecretString = exports.checkChecksum = exports.calculateChecksum = exports.secretToEntropy = exports.entropyToSecret = exports.randomSecret = exports.randomEntropy = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
function randomEntropy() {
    return (0, utils_1.randomBytes)(16);
}
exports.randomEntropy = randomEntropy;
function calculateChecksum(position, value) {
    return (value * (position * 2 + 1)) % 9;
}
exports.calculateChecksum = calculateChecksum;
function checkChecksum(position, value, checksum) {
    let normalizedChecksum;
    let normalizedValue;
    if (typeof value === 'string') {
        if (value.length !== 6) {
            throw new Error('value must have a length of 6');
        }
        normalizedChecksum = parseInt(value.slice(5), 10);
        normalizedValue = parseInt(value.slice(0, 5), 10);
    }
    else {
        if (typeof checksum !== 'number') {
            throw new Error('checksum must be a number when value is a number');
        }
        normalizedChecksum = checksum;
        normalizedValue = value;
    }
    return (normalizedValue * (position * 2 + 1)) % 9 === normalizedChecksum;
}
exports.checkChecksum = checkChecksum;
function entropyToSecret(entropy) {
    const len = new Array(Math.ceil(entropy.length / 2));
    const chunks = Array.from(len, (_a, chunk) => {
        const buffChunk = entropy.slice(chunk * 2, (chunk + 1) * 2);
        const no = parseInt((0, utils_1.bytesToHex)(buffChunk), 16);
        const fill = '0'.repeat(5 - String(no).length);
        return fill + String(no) + String(calculateChecksum(chunk, no));
    });
    if (chunks.length !== 8) {
        throw new Error('Chucks must have 8 digits');
    }
    return chunks;
}
exports.entropyToSecret = entropyToSecret;
function randomSecret() {
    return entropyToSecret(randomEntropy());
}
exports.randomSecret = randomSecret;
function secretToEntropy(secret) {
    return (0, utils_1.concat)(secret.map((chunk, i) => {
        const no = Number(chunk.slice(0, 5));
        const checksum = Number(chunk.slice(5));
        if (chunk.length !== 6) {
            throw new Error('Invalid secret: number invalid');
        }
        if (!checkChecksum(i, no, checksum)) {
            throw new Error('Invalid secret part: checksum invalid');
        }
        const hex = `0000${no.toString(16)}`.slice(-4);
        return (0, utils_1.hexToBytes)(hex);
    }));
}
exports.secretToEntropy = secretToEntropy;
function parseSecretString(secret) {
    const normalizedSecret = secret.replace(/[^0-9]/gu, '');
    if (normalizedSecret.length !== 48) {
        throw new Error('Invalid secret string (should contain 8 blocks of 6 digits');
    }
    return Array.from(new Array(8), (_a, index) => {
        return normalizedSecret.slice(index * 6, (index + 1) * 6);
    });
}
exports.parseSecretString = parseSecretString;


/***/ }),

/***/ "../../node_modules/bignumber.js/bignumber.js":
/*!****************************************************!*\
  !*** ../../node_modules/bignumber.js/bignumber.js ***!
  \****************************************************/
/***/ (function(module, exports, __webpack_require__) {

var __WEBPACK_AMD_DEFINE_RESULT__;;(function (globalObject) {
  'use strict';

/*
 *      bignumber.js v9.1.2
 *      A JavaScript library for arbitrary-precision arithmetic.
 *      https://github.com/MikeMcl/bignumber.js
 *      Copyright (c) 2022 Michael Mclaughlin <M8ch88l@gmail.com>
 *      MIT Licensed.
 *
 *      BigNumber.prototype methods     |  BigNumber methods
 *                                      |
 *      absoluteValue            abs    |  clone
 *      comparedTo                      |  config               set
 *      decimalPlaces            dp     |      DECIMAL_PLACES
 *      dividedBy                div    |      ROUNDING_MODE
 *      dividedToIntegerBy       idiv   |      EXPONENTIAL_AT
 *      exponentiatedBy          pow    |      RANGE
 *      integerValue                    |      CRYPTO
 *      isEqualTo                eq     |      MODULO_MODE
 *      isFinite                        |      POW_PRECISION
 *      isGreaterThan            gt     |      FORMAT
 *      isGreaterThanOrEqualTo   gte    |      ALPHABET
 *      isInteger                       |  isBigNumber
 *      isLessThan               lt     |  maximum              max
 *      isLessThanOrEqualTo      lte    |  minimum              min
 *      isNaN                           |  random
 *      isNegative                      |  sum
 *      isPositive                      |
 *      isZero                          |
 *      minus                           |
 *      modulo                   mod    |
 *      multipliedBy             times  |
 *      negated                         |
 *      plus                            |
 *      precision                sd     |
 *      shiftedBy                       |
 *      squareRoot               sqrt   |
 *      toExponential                   |
 *      toFixed                         |
 *      toFormat                        |
 *      toFraction                      |
 *      toJSON                          |
 *      toNumber                        |
 *      toPrecision                     |
 *      toString                        |
 *      valueOf                         |
 *
 */


  var BigNumber,
    isNumeric = /^-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i,
    mathceil = Math.ceil,
    mathfloor = Math.floor,

    bignumberError = '[BigNumber Error] ',
    tooManyDigits = bignumberError + 'Number primitive has more than 15 significant digits: ',

    BASE = 1e14,
    LOG_BASE = 14,
    MAX_SAFE_INTEGER = 0x1fffffffffffff,         // 2^53 - 1
    // MAX_INT32 = 0x7fffffff,                   // 2^31 - 1
    POWS_TEN = [1, 10, 100, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9, 1e10, 1e11, 1e12, 1e13],
    SQRT_BASE = 1e7,

    // EDITABLE
    // The limit on the value of DECIMAL_PLACES, TO_EXP_NEG, TO_EXP_POS, MIN_EXP, MAX_EXP, and
    // the arguments to toExponential, toFixed, toFormat, and toPrecision.
    MAX = 1E9;                                   // 0 to MAX_INT32


  /*
   * Create and return a BigNumber constructor.
   */
  function clone(configObject) {
    var div, convertBase, parseNumeric,
      P = BigNumber.prototype = { constructor: BigNumber, toString: null, valueOf: null },
      ONE = new BigNumber(1),


      //----------------------------- EDITABLE CONFIG DEFAULTS -------------------------------


      // The default values below must be integers within the inclusive ranges stated.
      // The values can also be changed at run-time using BigNumber.set.

      // The maximum number of decimal places for operations involving division.
      DECIMAL_PLACES = 20,                     // 0 to MAX

      // The rounding mode used when rounding to the above decimal places, and when using
      // toExponential, toFixed, toFormat and toPrecision, and round (default value).
      // UP         0 Away from zero.
      // DOWN       1 Towards zero.
      // CEIL       2 Towards +Infinity.
      // FLOOR      3 Towards -Infinity.
      // HALF_UP    4 Towards nearest neighbour. If equidistant, up.
      // HALF_DOWN  5 Towards nearest neighbour. If equidistant, down.
      // HALF_EVEN  6 Towards nearest neighbour. If equidistant, towards even neighbour.
      // HALF_CEIL  7 Towards nearest neighbour. If equidistant, towards +Infinity.
      // HALF_FLOOR 8 Towards nearest neighbour. If equidistant, towards -Infinity.
      ROUNDING_MODE = 4,                       // 0 to 8

      // EXPONENTIAL_AT : [TO_EXP_NEG , TO_EXP_POS]

      // The exponent value at and beneath which toString returns exponential notation.
      // Number type: -7
      TO_EXP_NEG = -7,                         // 0 to -MAX

      // The exponent value at and above which toString returns exponential notation.
      // Number type: 21
      TO_EXP_POS = 21,                         // 0 to MAX

      // RANGE : [MIN_EXP, MAX_EXP]

      // The minimum exponent value, beneath which underflow to zero occurs.
      // Number type: -324  (5e-324)
      MIN_EXP = -1e7,                          // -1 to -MAX

      // The maximum exponent value, above which overflow to Infinity occurs.
      // Number type:  308  (1.7976931348623157e+308)
      // For MAX_EXP > 1e7, e.g. new BigNumber('1e100000000').plus(1) may be slow.
      MAX_EXP = 1e7,                           // 1 to MAX

      // Whether to use cryptographically-secure random number generation, if available.
      CRYPTO = false,                          // true or false

      // The modulo mode used when calculating the modulus: a mod n.
      // The quotient (q = a / n) is calculated according to the corresponding rounding mode.
      // The remainder (r) is calculated as: r = a - n * q.
      //
      // UP        0 The remainder is positive if the dividend is negative, else is negative.
      // DOWN      1 The remainder has the same sign as the dividend.
      //             This modulo mode is commonly known as 'truncated division' and is
      //             equivalent to (a % n) in JavaScript.
      // FLOOR     3 The remainder has the same sign as the divisor (Python %).
      // HALF_EVEN 6 This modulo mode implements the IEEE 754 remainder function.
      // EUCLID    9 Euclidian division. q = sign(n) * floor(a / abs(n)).
      //             The remainder is always positive.
      //
      // The truncated division, floored division, Euclidian division and IEEE 754 remainder
      // modes are commonly used for the modulus operation.
      // Although the other rounding modes can also be used, they may not give useful results.
      MODULO_MODE = 1,                         // 0 to 9

      // The maximum number of significant digits of the result of the exponentiatedBy operation.
      // If POW_PRECISION is 0, there will be unlimited significant digits.
      POW_PRECISION = 0,                       // 0 to MAX

      // The format specification used by the BigNumber.prototype.toFormat method.
      FORMAT = {
        prefix: '',
        groupSize: 3,
        secondaryGroupSize: 0,
        groupSeparator: ',',
        decimalSeparator: '.',
        fractionGroupSize: 0,
        fractionGroupSeparator: '\xA0',        // non-breaking space
        suffix: ''
      },

      // The alphabet used for base conversion. It must be at least 2 characters long, with no '+',
      // '-', '.', whitespace, or repeated character.
      // '0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ$_'
      ALPHABET = '0123456789abcdefghijklmnopqrstuvwxyz',
      alphabetHasNormalDecimalDigits = true;


    //------------------------------------------------------------------------------------------


    // CONSTRUCTOR


    /*
     * The BigNumber constructor and exported function.
     * Create and return a new instance of a BigNumber object.
     *
     * v {number|string|BigNumber} A numeric value.
     * [b] {number} The base of v. Integer, 2 to ALPHABET.length inclusive.
     */
    function BigNumber(v, b) {
      var alphabet, c, caseChanged, e, i, isNum, len, str,
        x = this;

      // Enable constructor call without `new`.
      if (!(x instanceof BigNumber)) return new BigNumber(v, b);

      if (b == null) {

        if (v && v._isBigNumber === true) {
          x.s = v.s;

          if (!v.c || v.e > MAX_EXP) {
            x.c = x.e = null;
          } else if (v.e < MIN_EXP) {
            x.c = [x.e = 0];
          } else {
            x.e = v.e;
            x.c = v.c.slice();
          }

          return;
        }

        if ((isNum = typeof v == 'number') && v * 0 == 0) {

          // Use `1 / n` to handle minus zero also.
          x.s = 1 / v < 0 ? (v = -v, -1) : 1;

          // Fast path for integers, where n < 2147483648 (2**31).
          if (v === ~~v) {
            for (e = 0, i = v; i >= 10; i /= 10, e++);

            if (e > MAX_EXP) {
              x.c = x.e = null;
            } else {
              x.e = e;
              x.c = [v];
            }

            return;
          }

          str = String(v);
        } else {

          if (!isNumeric.test(str = String(v))) return parseNumeric(x, str, isNum);

          x.s = str.charCodeAt(0) == 45 ? (str = str.slice(1), -1) : 1;
        }

        // Decimal point?
        if ((e = str.indexOf('.')) > -1) str = str.replace('.', '');

        // Exponential form?
        if ((i = str.search(/e/i)) > 0) {

          // Determine exponent.
          if (e < 0) e = i;
          e += +str.slice(i + 1);
          str = str.substring(0, i);
        } else if (e < 0) {

          // Integer.
          e = str.length;
        }

      } else {

        // '[BigNumber Error] Base {not a primitive number|not an integer|out of range}: {b}'
        intCheck(b, 2, ALPHABET.length, 'Base');

        // Allow exponential notation to be used with base 10 argument, while
        // also rounding to DECIMAL_PLACES as with other bases.
        if (b == 10 && alphabetHasNormalDecimalDigits) {
          x = new BigNumber(v);
          return round(x, DECIMAL_PLACES + x.e + 1, ROUNDING_MODE);
        }

        str = String(v);

        if (isNum = typeof v == 'number') {

          // Avoid potential interpretation of Infinity and NaN as base 44+ values.
          if (v * 0 != 0) return parseNumeric(x, str, isNum, b);

          x.s = 1 / v < 0 ? (str = str.slice(1), -1) : 1;

          // '[BigNumber Error] Number primitive has more than 15 significant digits: {n}'
          if (BigNumber.DEBUG && str.replace(/^0\.0*|\./, '').length > 15) {
            throw Error
             (tooManyDigits + v);
          }
        } else {
          x.s = str.charCodeAt(0) === 45 ? (str = str.slice(1), -1) : 1;
        }

        alphabet = ALPHABET.slice(0, b);
        e = i = 0;

        // Check that str is a valid base b number.
        // Don't use RegExp, so alphabet can contain special characters.
        for (len = str.length; i < len; i++) {
          if (alphabet.indexOf(c = str.charAt(i)) < 0) {
            if (c == '.') {

              // If '.' is not the first character and it has not be found before.
              if (i > e) {
                e = len;
                continue;
              }
            } else if (!caseChanged) {

              // Allow e.g. hexadecimal 'FF' as well as 'ff'.
              if (str == str.toUpperCase() && (str = str.toLowerCase()) ||
                  str == str.toLowerCase() && (str = str.toUpperCase())) {
                caseChanged = true;
                i = -1;
                e = 0;
                continue;
              }
            }

            return parseNumeric(x, String(v), isNum, b);
          }
        }

        // Prevent later check for length on converted number.
        isNum = false;
        str = convertBase(str, b, 10, x.s);

        // Decimal point?
        if ((e = str.indexOf('.')) > -1) str = str.replace('.', '');
        else e = str.length;
      }

      // Determine leading zeros.
      for (i = 0; str.charCodeAt(i) === 48; i++);

      // Determine trailing zeros.
      for (len = str.length; str.charCodeAt(--len) === 48;);

      if (str = str.slice(i, ++len)) {
        len -= i;

        // '[BigNumber Error] Number primitive has more than 15 significant digits: {n}'
        if (isNum && BigNumber.DEBUG &&
          len > 15 && (v > MAX_SAFE_INTEGER || v !== mathfloor(v))) {
            throw Error
             (tooManyDigits + (x.s * v));
        }

         // Overflow?
        if ((e = e - i - 1) > MAX_EXP) {

          // Infinity.
          x.c = x.e = null;

        // Underflow?
        } else if (e < MIN_EXP) {

          // Zero.
          x.c = [x.e = 0];
        } else {
          x.e = e;
          x.c = [];

          // Transform base

          // e is the base 10 exponent.
          // i is where to slice str to get the first element of the coefficient array.
          i = (e + 1) % LOG_BASE;
          if (e < 0) i += LOG_BASE;  // i < 1

          if (i < len) {
            if (i) x.c.push(+str.slice(0, i));

            for (len -= LOG_BASE; i < len;) {
              x.c.push(+str.slice(i, i += LOG_BASE));
            }

            i = LOG_BASE - (str = str.slice(i)).length;
          } else {
            i -= len;
          }

          for (; i--; str += '0');
          x.c.push(+str);
        }
      } else {

        // Zero.
        x.c = [x.e = 0];
      }
    }


    // CONSTRUCTOR PROPERTIES


    BigNumber.clone = clone;

    BigNumber.ROUND_UP = 0;
    BigNumber.ROUND_DOWN = 1;
    BigNumber.ROUND_CEIL = 2;
    BigNumber.ROUND_FLOOR = 3;
    BigNumber.ROUND_HALF_UP = 4;
    BigNumber.ROUND_HALF_DOWN = 5;
    BigNumber.ROUND_HALF_EVEN = 6;
    BigNumber.ROUND_HALF_CEIL = 7;
    BigNumber.ROUND_HALF_FLOOR = 8;
    BigNumber.EUCLID = 9;


    /*
     * Configure infrequently-changing library-wide settings.
     *
     * Accept an object with the following optional properties (if the value of a property is
     * a number, it must be an integer within the inclusive range stated):
     *
     *   DECIMAL_PLACES   {number}           0 to MAX
     *   ROUNDING_MODE    {number}           0 to 8
     *   EXPONENTIAL_AT   {number|number[]}  -MAX to MAX  or  [-MAX to 0, 0 to MAX]
     *   RANGE            {number|number[]}  -MAX to MAX (not zero)  or  [-MAX to -1, 1 to MAX]
     *   CRYPTO           {boolean}          true or false
     *   MODULO_MODE      {number}           0 to 9
     *   POW_PRECISION       {number}           0 to MAX
     *   ALPHABET         {string}           A string of two or more unique characters which does
     *                                       not contain '.'.
     *   FORMAT           {object}           An object with some of the following properties:
     *     prefix                 {string}
     *     groupSize              {number}
     *     secondaryGroupSize     {number}
     *     groupSeparator         {string}
     *     decimalSeparator       {string}
     *     fractionGroupSize      {number}
     *     fractionGroupSeparator {string}
     *     suffix                 {string}
     *
     * (The values assigned to the above FORMAT object properties are not checked for validity.)
     *
     * E.g.
     * BigNumber.config({ DECIMAL_PLACES : 20, ROUNDING_MODE : 4 })
     *
     * Ignore properties/parameters set to null or undefined, except for ALPHABET.
     *
     * Return an object with the properties current values.
     */
    BigNumber.config = BigNumber.set = function (obj) {
      var p, v;

      if (obj != null) {

        if (typeof obj == 'object') {

          // DECIMAL_PLACES {number} Integer, 0 to MAX inclusive.
          // '[BigNumber Error] DECIMAL_PLACES {not a primitive number|not an integer|out of range}: {v}'
          if (obj.hasOwnProperty(p = 'DECIMAL_PLACES')) {
            v = obj[p];
            intCheck(v, 0, MAX, p);
            DECIMAL_PLACES = v;
          }

          // ROUNDING_MODE {number} Integer, 0 to 8 inclusive.
          // '[BigNumber Error] ROUNDING_MODE {not a primitive number|not an integer|out of range}: {v}'
          if (obj.hasOwnProperty(p = 'ROUNDING_MODE')) {
            v = obj[p];
            intCheck(v, 0, 8, p);
            ROUNDING_MODE = v;
          }

          // EXPONENTIAL_AT {number|number[]}
          // Integer, -MAX to MAX inclusive or
          // [integer -MAX to 0 inclusive, 0 to MAX inclusive].
          // '[BigNumber Error] EXPONENTIAL_AT {not a primitive number|not an integer|out of range}: {v}'
          if (obj.hasOwnProperty(p = 'EXPONENTIAL_AT')) {
            v = obj[p];
            if (v && v.pop) {
              intCheck(v[0], -MAX, 0, p);
              intCheck(v[1], 0, MAX, p);
              TO_EXP_NEG = v[0];
              TO_EXP_POS = v[1];
            } else {
              intCheck(v, -MAX, MAX, p);
              TO_EXP_NEG = -(TO_EXP_POS = v < 0 ? -v : v);
            }
          }

          // RANGE {number|number[]} Non-zero integer, -MAX to MAX inclusive or
          // [integer -MAX to -1 inclusive, integer 1 to MAX inclusive].
          // '[BigNumber Error] RANGE {not a primitive number|not an integer|out of range|cannot be zero}: {v}'
          if (obj.hasOwnProperty(p = 'RANGE')) {
            v = obj[p];
            if (v && v.pop) {
              intCheck(v[0], -MAX, -1, p);
              intCheck(v[1], 1, MAX, p);
              MIN_EXP = v[0];
              MAX_EXP = v[1];
            } else {
              intCheck(v, -MAX, MAX, p);
              if (v) {
                MIN_EXP = -(MAX_EXP = v < 0 ? -v : v);
              } else {
                throw Error
                 (bignumberError + p + ' cannot be zero: ' + v);
              }
            }
          }

          // CRYPTO {boolean} true or false.
          // '[BigNumber Error] CRYPTO not true or false: {v}'
          // '[BigNumber Error] crypto unavailable'
          if (obj.hasOwnProperty(p = 'CRYPTO')) {
            v = obj[p];
            if (v === !!v) {
              if (v) {
                if (typeof crypto != 'undefined' && crypto &&
                 (crypto.getRandomValues || crypto.randomBytes)) {
                  CRYPTO = v;
                } else {
                  CRYPTO = !v;
                  throw Error
                   (bignumberError + 'crypto unavailable');
                }
              } else {
                CRYPTO = v;
              }
            } else {
              throw Error
               (bignumberError + p + ' not true or false: ' + v);
            }
          }

          // MODULO_MODE {number} Integer, 0 to 9 inclusive.
          // '[BigNumber Error] MODULO_MODE {not a primitive number|not an integer|out of range}: {v}'
          if (obj.hasOwnProperty(p = 'MODULO_MODE')) {
            v = obj[p];
            intCheck(v, 0, 9, p);
            MODULO_MODE = v;
          }

          // POW_PRECISION {number} Integer, 0 to MAX inclusive.
          // '[BigNumber Error] POW_PRECISION {not a primitive number|not an integer|out of range}: {v}'
          if (obj.hasOwnProperty(p = 'POW_PRECISION')) {
            v = obj[p];
            intCheck(v, 0, MAX, p);
            POW_PRECISION = v;
          }

          // FORMAT {object}
          // '[BigNumber Error] FORMAT not an object: {v}'
          if (obj.hasOwnProperty(p = 'FORMAT')) {
            v = obj[p];
            if (typeof v == 'object') FORMAT = v;
            else throw Error
             (bignumberError + p + ' not an object: ' + v);
          }

          // ALPHABET {string}
          // '[BigNumber Error] ALPHABET invalid: {v}'
          if (obj.hasOwnProperty(p = 'ALPHABET')) {
            v = obj[p];

            // Disallow if less than two characters,
            // or if it contains '+', '-', '.', whitespace, or a repeated character.
            if (typeof v == 'string' && !/^.?$|[+\-.\s]|(.).*\1/.test(v)) {
              alphabetHasNormalDecimalDigits = v.slice(0, 10) == '0123456789';
              ALPHABET = v;
            } else {
              throw Error
               (bignumberError + p + ' invalid: ' + v);
            }
          }

        } else {

          // '[BigNumber Error] Object expected: {v}'
          throw Error
           (bignumberError + 'Object expected: ' + obj);
        }
      }

      return {
        DECIMAL_PLACES: DECIMAL_PLACES,
        ROUNDING_MODE: ROUNDING_MODE,
        EXPONENTIAL_AT: [TO_EXP_NEG, TO_EXP_POS],
        RANGE: [MIN_EXP, MAX_EXP],
        CRYPTO: CRYPTO,
        MODULO_MODE: MODULO_MODE,
        POW_PRECISION: POW_PRECISION,
        FORMAT: FORMAT,
        ALPHABET: ALPHABET
      };
    };


    /*
     * Return true if v is a BigNumber instance, otherwise return false.
     *
     * If BigNumber.DEBUG is true, throw if a BigNumber instance is not well-formed.
     *
     * v {any}
     *
     * '[BigNumber Error] Invalid BigNumber: {v}'
     */
    BigNumber.isBigNumber = function (v) {
      if (!v || v._isBigNumber !== true) return false;
      if (!BigNumber.DEBUG) return true;

      var i, n,
        c = v.c,
        e = v.e,
        s = v.s;

      out: if ({}.toString.call(c) == '[object Array]') {

        if ((s === 1 || s === -1) && e >= -MAX && e <= MAX && e === mathfloor(e)) {

          // If the first element is zero, the BigNumber value must be zero.
          if (c[0] === 0) {
            if (e === 0 && c.length === 1) return true;
            break out;
          }

          // Calculate number of digits that c[0] should have, based on the exponent.
          i = (e + 1) % LOG_BASE;
          if (i < 1) i += LOG_BASE;

          // Calculate number of digits of c[0].
          //if (Math.ceil(Math.log(c[0] + 1) / Math.LN10) == i) {
          if (String(c[0]).length == i) {

            for (i = 0; i < c.length; i++) {
              n = c[i];
              if (n < 0 || n >= BASE || n !== mathfloor(n)) break out;
            }

            // Last element cannot be zero, unless it is the only element.
            if (n !== 0) return true;
          }
        }

      // Infinity/NaN
      } else if (c === null && e === null && (s === null || s === 1 || s === -1)) {
        return true;
      }

      throw Error
        (bignumberError + 'Invalid BigNumber: ' + v);
    };


    /*
     * Return a new BigNumber whose value is the maximum of the arguments.
     *
     * arguments {number|string|BigNumber}
     */
    BigNumber.maximum = BigNumber.max = function () {
      return maxOrMin(arguments, -1);
    };


    /*
     * Return a new BigNumber whose value is the minimum of the arguments.
     *
     * arguments {number|string|BigNumber}
     */
    BigNumber.minimum = BigNumber.min = function () {
      return maxOrMin(arguments, 1);
    };


    /*
     * Return a new BigNumber with a random value equal to or greater than 0 and less than 1,
     * and with dp, or DECIMAL_PLACES if dp is omitted, decimal places (or less if trailing
     * zeros are produced).
     *
     * [dp] {number} Decimal places. Integer, 0 to MAX inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {dp}'
     * '[BigNumber Error] crypto unavailable'
     */
    BigNumber.random = (function () {
      var pow2_53 = 0x20000000000000;

      // Return a 53 bit integer n, where 0 <= n < 9007199254740992.
      // Check if Math.random() produces more than 32 bits of randomness.
      // If it does, assume at least 53 bits are produced, otherwise assume at least 30 bits.
      // 0x40000000 is 2^30, 0x800000 is 2^23, 0x1fffff is 2^21 - 1.
      var random53bitInt = (Math.random() * pow2_53) & 0x1fffff
       ? function () { return mathfloor(Math.random() * pow2_53); }
       : function () { return ((Math.random() * 0x40000000 | 0) * 0x800000) +
         (Math.random() * 0x800000 | 0); };

      return function (dp) {
        var a, b, e, k, v,
          i = 0,
          c = [],
          rand = new BigNumber(ONE);

        if (dp == null) dp = DECIMAL_PLACES;
        else intCheck(dp, 0, MAX);

        k = mathceil(dp / LOG_BASE);

        if (CRYPTO) {

          // Browsers supporting crypto.getRandomValues.
          if (crypto.getRandomValues) {

            a = crypto.getRandomValues(new Uint32Array(k *= 2));

            for (; i < k;) {

              // 53 bits:
              // ((Math.pow(2, 32) - 1) * Math.pow(2, 21)).toString(2)
              // 11111 11111111 11111111 11111111 11100000 00000000 00000000
              // ((Math.pow(2, 32) - 1) >>> 11).toString(2)
              //                                     11111 11111111 11111111
              // 0x20000 is 2^21.
              v = a[i] * 0x20000 + (a[i + 1] >>> 11);

              // Rejection sampling:
              // 0 <= v < 9007199254740992
              // Probability that v >= 9e15, is
              // 7199254740992 / 9007199254740992 ~= 0.0008, i.e. 1 in 1251
              if (v >= 9e15) {
                b = crypto.getRandomValues(new Uint32Array(2));
                a[i] = b[0];
                a[i + 1] = b[1];
              } else {

                // 0 <= v <= 8999999999999999
                // 0 <= (v % 1e14) <= 99999999999999
                c.push(v % 1e14);
                i += 2;
              }
            }
            i = k / 2;

          // Node.js supporting crypto.randomBytes.
          } else if (crypto.randomBytes) {

            // buffer
            a = crypto.randomBytes(k *= 7);

            for (; i < k;) {

              // 0x1000000000000 is 2^48, 0x10000000000 is 2^40
              // 0x100000000 is 2^32, 0x1000000 is 2^24
              // 11111 11111111 11111111 11111111 11111111 11111111 11111111
              // 0 <= v < 9007199254740992
              v = ((a[i] & 31) * 0x1000000000000) + (a[i + 1] * 0x10000000000) +
                 (a[i + 2] * 0x100000000) + (a[i + 3] * 0x1000000) +
                 (a[i + 4] << 16) + (a[i + 5] << 8) + a[i + 6];

              if (v >= 9e15) {
                crypto.randomBytes(7).copy(a, i);
              } else {

                // 0 <= (v % 1e14) <= 99999999999999
                c.push(v % 1e14);
                i += 7;
              }
            }
            i = k / 7;
          } else {
            CRYPTO = false;
            throw Error
             (bignumberError + 'crypto unavailable');
          }
        }

        // Use Math.random.
        if (!CRYPTO) {

          for (; i < k;) {
            v = random53bitInt();
            if (v < 9e15) c[i++] = v % 1e14;
          }
        }

        k = c[--i];
        dp %= LOG_BASE;

        // Convert trailing digits to zeros according to dp.
        if (k && dp) {
          v = POWS_TEN[LOG_BASE - dp];
          c[i] = mathfloor(k / v) * v;
        }

        // Remove trailing elements which are zero.
        for (; c[i] === 0; c.pop(), i--);

        // Zero?
        if (i < 0) {
          c = [e = 0];
        } else {

          // Remove leading elements which are zero and adjust exponent accordingly.
          for (e = -1 ; c[0] === 0; c.splice(0, 1), e -= LOG_BASE);

          // Count the digits of the first element of c to determine leading zeros, and...
          for (i = 1, v = c[0]; v >= 10; v /= 10, i++);

          // adjust the exponent accordingly.
          if (i < LOG_BASE) e -= LOG_BASE - i;
        }

        rand.e = e;
        rand.c = c;
        return rand;
      };
    })();


    /*
     * Return a BigNumber whose value is the sum of the arguments.
     *
     * arguments {number|string|BigNumber}
     */
    BigNumber.sum = function () {
      var i = 1,
        args = arguments,
        sum = new BigNumber(args[0]);
      for (; i < args.length;) sum = sum.plus(args[i++]);
      return sum;
    };


    // PRIVATE FUNCTIONS


    // Called by BigNumber and BigNumber.prototype.toString.
    convertBase = (function () {
      var decimal = '0123456789';

      /*
       * Convert string of baseIn to an array of numbers of baseOut.
       * Eg. toBaseOut('255', 10, 16) returns [15, 15].
       * Eg. toBaseOut('ff', 16, 10) returns [2, 5, 5].
       */
      function toBaseOut(str, baseIn, baseOut, alphabet) {
        var j,
          arr = [0],
          arrL,
          i = 0,
          len = str.length;

        for (; i < len;) {
          for (arrL = arr.length; arrL--; arr[arrL] *= baseIn);

          arr[0] += alphabet.indexOf(str.charAt(i++));

          for (j = 0; j < arr.length; j++) {

            if (arr[j] > baseOut - 1) {
              if (arr[j + 1] == null) arr[j + 1] = 0;
              arr[j + 1] += arr[j] / baseOut | 0;
              arr[j] %= baseOut;
            }
          }
        }

        return arr.reverse();
      }

      // Convert a numeric string of baseIn to a numeric string of baseOut.
      // If the caller is toString, we are converting from base 10 to baseOut.
      // If the caller is BigNumber, we are converting from baseIn to base 10.
      return function (str, baseIn, baseOut, sign, callerIsToString) {
        var alphabet, d, e, k, r, x, xc, y,
          i = str.indexOf('.'),
          dp = DECIMAL_PLACES,
          rm = ROUNDING_MODE;

        // Non-integer.
        if (i >= 0) {
          k = POW_PRECISION;

          // Unlimited precision.
          POW_PRECISION = 0;
          str = str.replace('.', '');
          y = new BigNumber(baseIn);
          x = y.pow(str.length - i);
          POW_PRECISION = k;

          // Convert str as if an integer, then restore the fraction part by dividing the
          // result by its base raised to a power.

          y.c = toBaseOut(toFixedPoint(coeffToString(x.c), x.e, '0'),
           10, baseOut, decimal);
          y.e = y.c.length;
        }

        // Convert the number as integer.

        xc = toBaseOut(str, baseIn, baseOut, callerIsToString
         ? (alphabet = ALPHABET, decimal)
         : (alphabet = decimal, ALPHABET));

        // xc now represents str as an integer and converted to baseOut. e is the exponent.
        e = k = xc.length;

        // Remove trailing zeros.
        for (; xc[--k] == 0; xc.pop());

        // Zero?
        if (!xc[0]) return alphabet.charAt(0);

        // Does str represent an integer? If so, no need for the division.
        if (i < 0) {
          --e;
        } else {
          x.c = xc;
          x.e = e;

          // The sign is needed for correct rounding.
          x.s = sign;
          x = div(x, y, dp, rm, baseOut);
          xc = x.c;
          r = x.r;
          e = x.e;
        }

        // xc now represents str converted to baseOut.

        // THe index of the rounding digit.
        d = e + dp + 1;

        // The rounding digit: the digit to the right of the digit that may be rounded up.
        i = xc[d];

        // Look at the rounding digits and mode to determine whether to round up.

        k = baseOut / 2;
        r = r || d < 0 || xc[d + 1] != null;

        r = rm < 4 ? (i != null || r) && (rm == 0 || rm == (x.s < 0 ? 3 : 2))
              : i > k || i == k &&(rm == 4 || r || rm == 6 && xc[d - 1] & 1 ||
               rm == (x.s < 0 ? 8 : 7));

        // If the index of the rounding digit is not greater than zero, or xc represents
        // zero, then the result of the base conversion is zero or, if rounding up, a value
        // such as 0.00001.
        if (d < 1 || !xc[0]) {

          // 1^-dp or 0
          str = r ? toFixedPoint(alphabet.charAt(1), -dp, alphabet.charAt(0)) : alphabet.charAt(0);
        } else {

          // Truncate xc to the required number of decimal places.
          xc.length = d;

          // Round up?
          if (r) {

            // Rounding up may mean the previous digit has to be rounded up and so on.
            for (--baseOut; ++xc[--d] > baseOut;) {
              xc[d] = 0;

              if (!d) {
                ++e;
                xc = [1].concat(xc);
              }
            }
          }

          // Determine trailing zeros.
          for (k = xc.length; !xc[--k];);

          // E.g. [4, 11, 15] becomes 4bf.
          for (i = 0, str = ''; i <= k; str += alphabet.charAt(xc[i++]));

          // Add leading zeros, decimal point and trailing zeros as required.
          str = toFixedPoint(str, e, alphabet.charAt(0));
        }

        // The caller will add the sign.
        return str;
      };
    })();


    // Perform division in the specified base. Called by div and convertBase.
    div = (function () {

      // Assume non-zero x and k.
      function multiply(x, k, base) {
        var m, temp, xlo, xhi,
          carry = 0,
          i = x.length,
          klo = k % SQRT_BASE,
          khi = k / SQRT_BASE | 0;

        for (x = x.slice(); i--;) {
          xlo = x[i] % SQRT_BASE;
          xhi = x[i] / SQRT_BASE | 0;
          m = khi * xlo + xhi * klo;
          temp = klo * xlo + ((m % SQRT_BASE) * SQRT_BASE) + carry;
          carry = (temp / base | 0) + (m / SQRT_BASE | 0) + khi * xhi;
          x[i] = temp % base;
        }

        if (carry) x = [carry].concat(x);

        return x;
      }

      function compare(a, b, aL, bL) {
        var i, cmp;

        if (aL != bL) {
          cmp = aL > bL ? 1 : -1;
        } else {

          for (i = cmp = 0; i < aL; i++) {

            if (a[i] != b[i]) {
              cmp = a[i] > b[i] ? 1 : -1;
              break;
            }
          }
        }

        return cmp;
      }

      function subtract(a, b, aL, base) {
        var i = 0;

        // Subtract b from a.
        for (; aL--;) {
          a[aL] -= i;
          i = a[aL] < b[aL] ? 1 : 0;
          a[aL] = i * base + a[aL] - b[aL];
        }

        // Remove leading zeros.
        for (; !a[0] && a.length > 1; a.splice(0, 1));
      }

      // x: dividend, y: divisor.
      return function (x, y, dp, rm, base) {
        var cmp, e, i, more, n, prod, prodL, q, qc, rem, remL, rem0, xi, xL, yc0,
          yL, yz,
          s = x.s == y.s ? 1 : -1,
          xc = x.c,
          yc = y.c;

        // Either NaN, Infinity or 0?
        if (!xc || !xc[0] || !yc || !yc[0]) {

          return new BigNumber(

           // Return NaN if either NaN, or both Infinity or 0.
           !x.s || !y.s || (xc ? yc && xc[0] == yc[0] : !yc) ? NaN :

            // Return ±0 if x is ±0 or y is ±Infinity, or return ±Infinity as y is ±0.
            xc && xc[0] == 0 || !yc ? s * 0 : s / 0
         );
        }

        q = new BigNumber(s);
        qc = q.c = [];
        e = x.e - y.e;
        s = dp + e + 1;

        if (!base) {
          base = BASE;
          e = bitFloor(x.e / LOG_BASE) - bitFloor(y.e / LOG_BASE);
          s = s / LOG_BASE | 0;
        }

        // Result exponent may be one less then the current value of e.
        // The coefficients of the BigNumbers from convertBase may have trailing zeros.
        for (i = 0; yc[i] == (xc[i] || 0); i++);

        if (yc[i] > (xc[i] || 0)) e--;

        if (s < 0) {
          qc.push(1);
          more = true;
        } else {
          xL = xc.length;
          yL = yc.length;
          i = 0;
          s += 2;

          // Normalise xc and yc so highest order digit of yc is >= base / 2.

          n = mathfloor(base / (yc[0] + 1));

          // Not necessary, but to handle odd bases where yc[0] == (base / 2) - 1.
          // if (n > 1 || n++ == 1 && yc[0] < base / 2) {
          if (n > 1) {
            yc = multiply(yc, n, base);
            xc = multiply(xc, n, base);
            yL = yc.length;
            xL = xc.length;
          }

          xi = yL;
          rem = xc.slice(0, yL);
          remL = rem.length;

          // Add zeros to make remainder as long as divisor.
          for (; remL < yL; rem[remL++] = 0);
          yz = yc.slice();
          yz = [0].concat(yz);
          yc0 = yc[0];
          if (yc[1] >= base / 2) yc0++;
          // Not necessary, but to prevent trial digit n > base, when using base 3.
          // else if (base == 3 && yc0 == 1) yc0 = 1 + 1e-15;

          do {
            n = 0;

            // Compare divisor and remainder.
            cmp = compare(yc, rem, yL, remL);

            // If divisor < remainder.
            if (cmp < 0) {

              // Calculate trial digit, n.

              rem0 = rem[0];
              if (yL != remL) rem0 = rem0 * base + (rem[1] || 0);

              // n is how many times the divisor goes into the current remainder.
              n = mathfloor(rem0 / yc0);

              //  Algorithm:
              //  product = divisor multiplied by trial digit (n).
              //  Compare product and remainder.
              //  If product is greater than remainder:
              //    Subtract divisor from product, decrement trial digit.
              //  Subtract product from remainder.
              //  If product was less than remainder at the last compare:
              //    Compare new remainder and divisor.
              //    If remainder is greater than divisor:
              //      Subtract divisor from remainder, increment trial digit.

              if (n > 1) {

                // n may be > base only when base is 3.
                if (n >= base) n = base - 1;

                // product = divisor * trial digit.
                prod = multiply(yc, n, base);
                prodL = prod.length;
                remL = rem.length;

                // Compare product and remainder.
                // If product > remainder then trial digit n too high.
                // n is 1 too high about 5% of the time, and is not known to have
                // ever been more than 1 too high.
                while (compare(prod, rem, prodL, remL) == 1) {
                  n--;

                  // Subtract divisor from product.
                  subtract(prod, yL < prodL ? yz : yc, prodL, base);
                  prodL = prod.length;
                  cmp = 1;
                }
              } else {

                // n is 0 or 1, cmp is -1.
                // If n is 0, there is no need to compare yc and rem again below,
                // so change cmp to 1 to avoid it.
                // If n is 1, leave cmp as -1, so yc and rem are compared again.
                if (n == 0) {

                  // divisor < remainder, so n must be at least 1.
                  cmp = n = 1;
                }

                // product = divisor
                prod = yc.slice();
                prodL = prod.length;
              }

              if (prodL < remL) prod = [0].concat(prod);

              // Subtract product from remainder.
              subtract(rem, prod, remL, base);
              remL = rem.length;

               // If product was < remainder.
              if (cmp == -1) {

                // Compare divisor and new remainder.
                // If divisor < new remainder, subtract divisor from remainder.
                // Trial digit n too low.
                // n is 1 too low about 5% of the time, and very rarely 2 too low.
                while (compare(yc, rem, yL, remL) < 1) {
                  n++;

                  // Subtract divisor from remainder.
                  subtract(rem, yL < remL ? yz : yc, remL, base);
                  remL = rem.length;
                }
              }
            } else if (cmp === 0) {
              n++;
              rem = [0];
            } // else cmp === 1 and n will be 0

            // Add the next digit, n, to the result array.
            qc[i++] = n;

            // Update the remainder.
            if (rem[0]) {
              rem[remL++] = xc[xi] || 0;
            } else {
              rem = [xc[xi]];
              remL = 1;
            }
          } while ((xi++ < xL || rem[0] != null) && s--);

          more = rem[0] != null;

          // Leading zero?
          if (!qc[0]) qc.splice(0, 1);
        }

        if (base == BASE) {

          // To calculate q.e, first get the number of digits of qc[0].
          for (i = 1, s = qc[0]; s >= 10; s /= 10, i++);

          round(q, dp + (q.e = i + e * LOG_BASE - 1) + 1, rm, more);

        // Caller is convertBase.
        } else {
          q.e = e;
          q.r = +more;
        }

        return q;
      };
    })();


    /*
     * Return a string representing the value of BigNumber n in fixed-point or exponential
     * notation rounded to the specified decimal places or significant digits.
     *
     * n: a BigNumber.
     * i: the index of the last digit required (i.e. the digit that may be rounded up).
     * rm: the rounding mode.
     * id: 1 (toExponential) or 2 (toPrecision).
     */
    function format(n, i, rm, id) {
      var c0, e, ne, len, str;

      if (rm == null) rm = ROUNDING_MODE;
      else intCheck(rm, 0, 8);

      if (!n.c) return n.toString();

      c0 = n.c[0];
      ne = n.e;

      if (i == null) {
        str = coeffToString(n.c);
        str = id == 1 || id == 2 && (ne <= TO_EXP_NEG || ne >= TO_EXP_POS)
         ? toExponential(str, ne)
         : toFixedPoint(str, ne, '0');
      } else {
        n = round(new BigNumber(n), i, rm);

        // n.e may have changed if the value was rounded up.
        e = n.e;

        str = coeffToString(n.c);
        len = str.length;

        // toPrecision returns exponential notation if the number of significant digits
        // specified is less than the number of digits necessary to represent the integer
        // part of the value in fixed-point notation.

        // Exponential notation.
        if (id == 1 || id == 2 && (i <= e || e <= TO_EXP_NEG)) {

          // Append zeros?
          for (; len < i; str += '0', len++);
          str = toExponential(str, e);

        // Fixed-point notation.
        } else {
          i -= ne;
          str = toFixedPoint(str, e, '0');

          // Append zeros?
          if (e + 1 > len) {
            if (--i > 0) for (str += '.'; i--; str += '0');
          } else {
            i += e - len;
            if (i > 0) {
              if (e + 1 == len) str += '.';
              for (; i--; str += '0');
            }
          }
        }
      }

      return n.s < 0 && c0 ? '-' + str : str;
    }


    // Handle BigNumber.max and BigNumber.min.
    // If any number is NaN, return NaN.
    function maxOrMin(args, n) {
      var k, y,
        i = 1,
        x = new BigNumber(args[0]);

      for (; i < args.length; i++) {
        y = new BigNumber(args[i]);
        if (!y.s || (k = compare(x, y)) === n || k === 0 && x.s === n) {
          x = y;
        }
      }

      return x;
    }


    /*
     * Strip trailing zeros, calculate base 10 exponent and check against MIN_EXP and MAX_EXP.
     * Called by minus, plus and times.
     */
    function normalise(n, c, e) {
      var i = 1,
        j = c.length;

       // Remove trailing zeros.
      for (; !c[--j]; c.pop());

      // Calculate the base 10 exponent. First get the number of digits of c[0].
      for (j = c[0]; j >= 10; j /= 10, i++);

      // Overflow?
      if ((e = i + e * LOG_BASE - 1) > MAX_EXP) {

        // Infinity.
        n.c = n.e = null;

      // Underflow?
      } else if (e < MIN_EXP) {

        // Zero.
        n.c = [n.e = 0];
      } else {
        n.e = e;
        n.c = c;
      }

      return n;
    }


    // Handle values that fail the validity test in BigNumber.
    parseNumeric = (function () {
      var basePrefix = /^(-?)0([xbo])(?=\w[\w.]*$)/i,
        dotAfter = /^([^.]+)\.$/,
        dotBefore = /^\.([^.]+)$/,
        isInfinityOrNaN = /^-?(Infinity|NaN)$/,
        whitespaceOrPlus = /^\s*\+(?=[\w.])|^\s+|\s+$/g;

      return function (x, str, isNum, b) {
        var base,
          s = isNum ? str : str.replace(whitespaceOrPlus, '');

        // No exception on ±Infinity or NaN.
        if (isInfinityOrNaN.test(s)) {
          x.s = isNaN(s) ? null : s < 0 ? -1 : 1;
        } else {
          if (!isNum) {

            // basePrefix = /^(-?)0([xbo])(?=\w[\w.]*$)/i
            s = s.replace(basePrefix, function (m, p1, p2) {
              base = (p2 = p2.toLowerCase()) == 'x' ? 16 : p2 == 'b' ? 2 : 8;
              return !b || b == base ? p1 : m;
            });

            if (b) {
              base = b;

              // E.g. '1.' to '1', '.1' to '0.1'
              s = s.replace(dotAfter, '$1').replace(dotBefore, '0.$1');
            }

            if (str != s) return new BigNumber(s, base);
          }

          // '[BigNumber Error] Not a number: {n}'
          // '[BigNumber Error] Not a base {b} number: {n}'
          if (BigNumber.DEBUG) {
            throw Error
              (bignumberError + 'Not a' + (b ? ' base ' + b : '') + ' number: ' + str);
          }

          // NaN
          x.s = null;
        }

        x.c = x.e = null;
      }
    })();


    /*
     * Round x to sd significant digits using rounding mode rm. Check for over/under-flow.
     * If r is truthy, it is known that there are more digits after the rounding digit.
     */
    function round(x, sd, rm, r) {
      var d, i, j, k, n, ni, rd,
        xc = x.c,
        pows10 = POWS_TEN;

      // if x is not Infinity or NaN...
      if (xc) {

        // rd is the rounding digit, i.e. the digit after the digit that may be rounded up.
        // n is a base 1e14 number, the value of the element of array x.c containing rd.
        // ni is the index of n within x.c.
        // d is the number of digits of n.
        // i is the index of rd within n including leading zeros.
        // j is the actual index of rd within n (if < 0, rd is a leading zero).
        out: {

          // Get the number of digits of the first element of xc.
          for (d = 1, k = xc[0]; k >= 10; k /= 10, d++);
          i = sd - d;

          // If the rounding digit is in the first element of xc...
          if (i < 0) {
            i += LOG_BASE;
            j = sd;
            n = xc[ni = 0];

            // Get the rounding digit at index j of n.
            rd = mathfloor(n / pows10[d - j - 1] % 10);
          } else {
            ni = mathceil((i + 1) / LOG_BASE);

            if (ni >= xc.length) {

              if (r) {

                // Needed by sqrt.
                for (; xc.length <= ni; xc.push(0));
                n = rd = 0;
                d = 1;
                i %= LOG_BASE;
                j = i - LOG_BASE + 1;
              } else {
                break out;
              }
            } else {
              n = k = xc[ni];

              // Get the number of digits of n.
              for (d = 1; k >= 10; k /= 10, d++);

              // Get the index of rd within n.
              i %= LOG_BASE;

              // Get the index of rd within n, adjusted for leading zeros.
              // The number of leading zeros of n is given by LOG_BASE - d.
              j = i - LOG_BASE + d;

              // Get the rounding digit at index j of n.
              rd = j < 0 ? 0 : mathfloor(n / pows10[d - j - 1] % 10);
            }
          }

          r = r || sd < 0 ||

          // Are there any non-zero digits after the rounding digit?
          // The expression  n % pows10[d - j - 1]  returns all digits of n to the right
          // of the digit at j, e.g. if n is 908714 and j is 2, the expression gives 714.
           xc[ni + 1] != null || (j < 0 ? n : n % pows10[d - j - 1]);

          r = rm < 4
           ? (rd || r) && (rm == 0 || rm == (x.s < 0 ? 3 : 2))
           : rd > 5 || rd == 5 && (rm == 4 || r || rm == 6 &&

            // Check whether the digit to the left of the rounding digit is odd.
            ((i > 0 ? j > 0 ? n / pows10[d - j] : 0 : xc[ni - 1]) % 10) & 1 ||
             rm == (x.s < 0 ? 8 : 7));

          if (sd < 1 || !xc[0]) {
            xc.length = 0;

            if (r) {

              // Convert sd to decimal places.
              sd -= x.e + 1;

              // 1, 0.1, 0.01, 0.001, 0.0001 etc.
              xc[0] = pows10[(LOG_BASE - sd % LOG_BASE) % LOG_BASE];
              x.e = -sd || 0;
            } else {

              // Zero.
              xc[0] = x.e = 0;
            }

            return x;
          }

          // Remove excess digits.
          if (i == 0) {
            xc.length = ni;
            k = 1;
            ni--;
          } else {
            xc.length = ni + 1;
            k = pows10[LOG_BASE - i];

            // E.g. 56700 becomes 56000 if 7 is the rounding digit.
            // j > 0 means i > number of leading zeros of n.
            xc[ni] = j > 0 ? mathfloor(n / pows10[d - j] % pows10[j]) * k : 0;
          }

          // Round up?
          if (r) {

            for (; ;) {

              // If the digit to be rounded up is in the first element of xc...
              if (ni == 0) {

                // i will be the length of xc[0] before k is added.
                for (i = 1, j = xc[0]; j >= 10; j /= 10, i++);
                j = xc[0] += k;
                for (k = 1; j >= 10; j /= 10, k++);

                // if i != k the length has increased.
                if (i != k) {
                  x.e++;
                  if (xc[0] == BASE) xc[0] = 1;
                }

                break;
              } else {
                xc[ni] += k;
                if (xc[ni] != BASE) break;
                xc[ni--] = 0;
                k = 1;
              }
            }
          }

          // Remove trailing zeros.
          for (i = xc.length; xc[--i] === 0; xc.pop());
        }

        // Overflow? Infinity.
        if (x.e > MAX_EXP) {
          x.c = x.e = null;

        // Underflow? Zero.
        } else if (x.e < MIN_EXP) {
          x.c = [x.e = 0];
        }
      }

      return x;
    }


    function valueOf(n) {
      var str,
        e = n.e;

      if (e === null) return n.toString();

      str = coeffToString(n.c);

      str = e <= TO_EXP_NEG || e >= TO_EXP_POS
        ? toExponential(str, e)
        : toFixedPoint(str, e, '0');

      return n.s < 0 ? '-' + str : str;
    }


    // PROTOTYPE/INSTANCE METHODS


    /*
     * Return a new BigNumber whose value is the absolute value of this BigNumber.
     */
    P.absoluteValue = P.abs = function () {
      var x = new BigNumber(this);
      if (x.s < 0) x.s = 1;
      return x;
    };


    /*
     * Return
     *   1 if the value of this BigNumber is greater than the value of BigNumber(y, b),
     *   -1 if the value of this BigNumber is less than the value of BigNumber(y, b),
     *   0 if they have the same value,
     *   or null if the value of either is NaN.
     */
    P.comparedTo = function (y, b) {
      return compare(this, new BigNumber(y, b));
    };


    /*
     * If dp is undefined or null or true or false, return the number of decimal places of the
     * value of this BigNumber, or null if the value of this BigNumber is ±Infinity or NaN.
     *
     * Otherwise, if dp is a number, return a new BigNumber whose value is the value of this
     * BigNumber rounded to a maximum of dp decimal places using rounding mode rm, or
     * ROUNDING_MODE if rm is omitted.
     *
     * [dp] {number} Decimal places: integer, 0 to MAX inclusive.
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {dp|rm}'
     */
    P.decimalPlaces = P.dp = function (dp, rm) {
      var c, n, v,
        x = this;

      if (dp != null) {
        intCheck(dp, 0, MAX);
        if (rm == null) rm = ROUNDING_MODE;
        else intCheck(rm, 0, 8);

        return round(new BigNumber(x), dp + x.e + 1, rm);
      }

      if (!(c = x.c)) return null;
      n = ((v = c.length - 1) - bitFloor(this.e / LOG_BASE)) * LOG_BASE;

      // Subtract the number of trailing zeros of the last number.
      if (v = c[v]) for (; v % 10 == 0; v /= 10, n--);
      if (n < 0) n = 0;

      return n;
    };


    /*
     *  n / 0 = I
     *  n / N = N
     *  n / I = 0
     *  0 / n = 0
     *  0 / 0 = N
     *  0 / N = N
     *  0 / I = 0
     *  N / n = N
     *  N / 0 = N
     *  N / N = N
     *  N / I = N
     *  I / n = I
     *  I / 0 = I
     *  I / N = N
     *  I / I = N
     *
     * Return a new BigNumber whose value is the value of this BigNumber divided by the value of
     * BigNumber(y, b), rounded according to DECIMAL_PLACES and ROUNDING_MODE.
     */
    P.dividedBy = P.div = function (y, b) {
      return div(this, new BigNumber(y, b), DECIMAL_PLACES, ROUNDING_MODE);
    };


    /*
     * Return a new BigNumber whose value is the integer part of dividing the value of this
     * BigNumber by the value of BigNumber(y, b).
     */
    P.dividedToIntegerBy = P.idiv = function (y, b) {
      return div(this, new BigNumber(y, b), 0, 1);
    };


    /*
     * Return a BigNumber whose value is the value of this BigNumber exponentiated by n.
     *
     * If m is present, return the result modulo m.
     * If n is negative round according to DECIMAL_PLACES and ROUNDING_MODE.
     * If POW_PRECISION is non-zero and m is not present, round to POW_PRECISION using ROUNDING_MODE.
     *
     * The modular power operation works efficiently when x, n, and m are integers, otherwise it
     * is equivalent to calculating x.exponentiatedBy(n).modulo(m) with a POW_PRECISION of 0.
     *
     * n {number|string|BigNumber} The exponent. An integer.
     * [m] {number|string|BigNumber} The modulus.
     *
     * '[BigNumber Error] Exponent not an integer: {n}'
     */
    P.exponentiatedBy = P.pow = function (n, m) {
      var half, isModExp, i, k, more, nIsBig, nIsNeg, nIsOdd, y,
        x = this;

      n = new BigNumber(n);

      // Allow NaN and ±Infinity, but not other non-integers.
      if (n.c && !n.isInteger()) {
        throw Error
          (bignumberError + 'Exponent not an integer: ' + valueOf(n));
      }

      if (m != null) m = new BigNumber(m);

      // Exponent of MAX_SAFE_INTEGER is 15.
      nIsBig = n.e > 14;

      // If x is NaN, ±Infinity, ±0 or ±1, or n is ±Infinity, NaN or ±0.
      if (!x.c || !x.c[0] || x.c[0] == 1 && !x.e && x.c.length == 1 || !n.c || !n.c[0]) {

        // The sign of the result of pow when x is negative depends on the evenness of n.
        // If +n overflows to ±Infinity, the evenness of n would be not be known.
        y = new BigNumber(Math.pow(+valueOf(x), nIsBig ? n.s * (2 - isOdd(n)) : +valueOf(n)));
        return m ? y.mod(m) : y;
      }

      nIsNeg = n.s < 0;

      if (m) {

        // x % m returns NaN if abs(m) is zero, or m is NaN.
        if (m.c ? !m.c[0] : !m.s) return new BigNumber(NaN);

        isModExp = !nIsNeg && x.isInteger() && m.isInteger();

        if (isModExp) x = x.mod(m);

      // Overflow to ±Infinity: >=2**1e10 or >=1.0000024**1e15.
      // Underflow to ±0: <=0.79**1e10 or <=0.9999975**1e15.
      } else if (n.e > 9 && (x.e > 0 || x.e < -1 || (x.e == 0
        // [1, 240000000]
        ? x.c[0] > 1 || nIsBig && x.c[1] >= 24e7
        // [80000000000000]  [99999750000000]
        : x.c[0] < 8e13 || nIsBig && x.c[0] <= 9999975e7))) {

        // If x is negative and n is odd, k = -0, else k = 0.
        k = x.s < 0 && isOdd(n) ? -0 : 0;

        // If x >= 1, k = ±Infinity.
        if (x.e > -1) k = 1 / k;

        // If n is negative return ±0, else return ±Infinity.
        return new BigNumber(nIsNeg ? 1 / k : k);

      } else if (POW_PRECISION) {

        // Truncating each coefficient array to a length of k after each multiplication
        // equates to truncating significant digits to POW_PRECISION + [28, 41],
        // i.e. there will be a minimum of 28 guard digits retained.
        k = mathceil(POW_PRECISION / LOG_BASE + 2);
      }

      if (nIsBig) {
        half = new BigNumber(0.5);
        if (nIsNeg) n.s = 1;
        nIsOdd = isOdd(n);
      } else {
        i = Math.abs(+valueOf(n));
        nIsOdd = i % 2;
      }

      y = new BigNumber(ONE);

      // Performs 54 loop iterations for n of 9007199254740991.
      for (; ;) {

        if (nIsOdd) {
          y = y.times(x);
          if (!y.c) break;

          if (k) {
            if (y.c.length > k) y.c.length = k;
          } else if (isModExp) {
            y = y.mod(m);    //y = y.minus(div(y, m, 0, MODULO_MODE).times(m));
          }
        }

        if (i) {
          i = mathfloor(i / 2);
          if (i === 0) break;
          nIsOdd = i % 2;
        } else {
          n = n.times(half);
          round(n, n.e + 1, 1);

          if (n.e > 14) {
            nIsOdd = isOdd(n);
          } else {
            i = +valueOf(n);
            if (i === 0) break;
            nIsOdd = i % 2;
          }
        }

        x = x.times(x);

        if (k) {
          if (x.c && x.c.length > k) x.c.length = k;
        } else if (isModExp) {
          x = x.mod(m);    //x = x.minus(div(x, m, 0, MODULO_MODE).times(m));
        }
      }

      if (isModExp) return y;
      if (nIsNeg) y = ONE.div(y);

      return m ? y.mod(m) : k ? round(y, POW_PRECISION, ROUNDING_MODE, more) : y;
    };


    /*
     * Return a new BigNumber whose value is the value of this BigNumber rounded to an integer
     * using rounding mode rm, or ROUNDING_MODE if rm is omitted.
     *
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {rm}'
     */
    P.integerValue = function (rm) {
      var n = new BigNumber(this);
      if (rm == null) rm = ROUNDING_MODE;
      else intCheck(rm, 0, 8);
      return round(n, n.e + 1, rm);
    };


    /*
     * Return true if the value of this BigNumber is equal to the value of BigNumber(y, b),
     * otherwise return false.
     */
    P.isEqualTo = P.eq = function (y, b) {
      return compare(this, new BigNumber(y, b)) === 0;
    };


    /*
     * Return true if the value of this BigNumber is a finite number, otherwise return false.
     */
    P.isFinite = function () {
      return !!this.c;
    };


    /*
     * Return true if the value of this BigNumber is greater than the value of BigNumber(y, b),
     * otherwise return false.
     */
    P.isGreaterThan = P.gt = function (y, b) {
      return compare(this, new BigNumber(y, b)) > 0;
    };


    /*
     * Return true if the value of this BigNumber is greater than or equal to the value of
     * BigNumber(y, b), otherwise return false.
     */
    P.isGreaterThanOrEqualTo = P.gte = function (y, b) {
      return (b = compare(this, new BigNumber(y, b))) === 1 || b === 0;

    };


    /*
     * Return true if the value of this BigNumber is an integer, otherwise return false.
     */
    P.isInteger = function () {
      return !!this.c && bitFloor(this.e / LOG_BASE) > this.c.length - 2;
    };


    /*
     * Return true if the value of this BigNumber is less than the value of BigNumber(y, b),
     * otherwise return false.
     */
    P.isLessThan = P.lt = function (y, b) {
      return compare(this, new BigNumber(y, b)) < 0;
    };


    /*
     * Return true if the value of this BigNumber is less than or equal to the value of
     * BigNumber(y, b), otherwise return false.
     */
    P.isLessThanOrEqualTo = P.lte = function (y, b) {
      return (b = compare(this, new BigNumber(y, b))) === -1 || b === 0;
    };


    /*
     * Return true if the value of this BigNumber is NaN, otherwise return false.
     */
    P.isNaN = function () {
      return !this.s;
    };


    /*
     * Return true if the value of this BigNumber is negative, otherwise return false.
     */
    P.isNegative = function () {
      return this.s < 0;
    };


    /*
     * Return true if the value of this BigNumber is positive, otherwise return false.
     */
    P.isPositive = function () {
      return this.s > 0;
    };


    /*
     * Return true if the value of this BigNumber is 0 or -0, otherwise return false.
     */
    P.isZero = function () {
      return !!this.c && this.c[0] == 0;
    };


    /*
     *  n - 0 = n
     *  n - N = N
     *  n - I = -I
     *  0 - n = -n
     *  0 - 0 = 0
     *  0 - N = N
     *  0 - I = -I
     *  N - n = N
     *  N - 0 = N
     *  N - N = N
     *  N - I = N
     *  I - n = I
     *  I - 0 = I
     *  I - N = N
     *  I - I = N
     *
     * Return a new BigNumber whose value is the value of this BigNumber minus the value of
     * BigNumber(y, b).
     */
    P.minus = function (y, b) {
      var i, j, t, xLTy,
        x = this,
        a = x.s;

      y = new BigNumber(y, b);
      b = y.s;

      // Either NaN?
      if (!a || !b) return new BigNumber(NaN);

      // Signs differ?
      if (a != b) {
        y.s = -b;
        return x.plus(y);
      }

      var xe = x.e / LOG_BASE,
        ye = y.e / LOG_BASE,
        xc = x.c,
        yc = y.c;

      if (!xe || !ye) {

        // Either Infinity?
        if (!xc || !yc) return xc ? (y.s = -b, y) : new BigNumber(yc ? x : NaN);

        // Either zero?
        if (!xc[0] || !yc[0]) {

          // Return y if y is non-zero, x if x is non-zero, or zero if both are zero.
          return yc[0] ? (y.s = -b, y) : new BigNumber(xc[0] ? x :

           // IEEE 754 (2008) 6.3: n - n = -0 when rounding to -Infinity
           ROUNDING_MODE == 3 ? -0 : 0);
        }
      }

      xe = bitFloor(xe);
      ye = bitFloor(ye);
      xc = xc.slice();

      // Determine which is the bigger number.
      if (a = xe - ye) {

        if (xLTy = a < 0) {
          a = -a;
          t = xc;
        } else {
          ye = xe;
          t = yc;
        }

        t.reverse();

        // Prepend zeros to equalise exponents.
        for (b = a; b--; t.push(0));
        t.reverse();
      } else {

        // Exponents equal. Check digit by digit.
        j = (xLTy = (a = xc.length) < (b = yc.length)) ? a : b;

        for (a = b = 0; b < j; b++) {

          if (xc[b] != yc[b]) {
            xLTy = xc[b] < yc[b];
            break;
          }
        }
      }

      // x < y? Point xc to the array of the bigger number.
      if (xLTy) {
        t = xc;
        xc = yc;
        yc = t;
        y.s = -y.s;
      }

      b = (j = yc.length) - (i = xc.length);

      // Append zeros to xc if shorter.
      // No need to add zeros to yc if shorter as subtract only needs to start at yc.length.
      if (b > 0) for (; b--; xc[i++] = 0);
      b = BASE - 1;

      // Subtract yc from xc.
      for (; j > a;) {

        if (xc[--j] < yc[j]) {
          for (i = j; i && !xc[--i]; xc[i] = b);
          --xc[i];
          xc[j] += BASE;
        }

        xc[j] -= yc[j];
      }

      // Remove leading zeros and adjust exponent accordingly.
      for (; xc[0] == 0; xc.splice(0, 1), --ye);

      // Zero?
      if (!xc[0]) {

        // Following IEEE 754 (2008) 6.3,
        // n - n = +0  but  n - n = -0  when rounding towards -Infinity.
        y.s = ROUNDING_MODE == 3 ? -1 : 1;
        y.c = [y.e = 0];
        return y;
      }

      // No need to check for Infinity as +x - +y != Infinity && -x - -y != Infinity
      // for finite x and y.
      return normalise(y, xc, ye);
    };


    /*
     *   n % 0 =  N
     *   n % N =  N
     *   n % I =  n
     *   0 % n =  0
     *  -0 % n = -0
     *   0 % 0 =  N
     *   0 % N =  N
     *   0 % I =  0
     *   N % n =  N
     *   N % 0 =  N
     *   N % N =  N
     *   N % I =  N
     *   I % n =  N
     *   I % 0 =  N
     *   I % N =  N
     *   I % I =  N
     *
     * Return a new BigNumber whose value is the value of this BigNumber modulo the value of
     * BigNumber(y, b). The result depends on the value of MODULO_MODE.
     */
    P.modulo = P.mod = function (y, b) {
      var q, s,
        x = this;

      y = new BigNumber(y, b);

      // Return NaN if x is Infinity or NaN, or y is NaN or zero.
      if (!x.c || !y.s || y.c && !y.c[0]) {
        return new BigNumber(NaN);

      // Return x if y is Infinity or x is zero.
      } else if (!y.c || x.c && !x.c[0]) {
        return new BigNumber(x);
      }

      if (MODULO_MODE == 9) {

        // Euclidian division: q = sign(y) * floor(x / abs(y))
        // r = x - qy    where  0 <= r < abs(y)
        s = y.s;
        y.s = 1;
        q = div(x, y, 0, 3);
        y.s = s;
        q.s *= s;
      } else {
        q = div(x, y, 0, MODULO_MODE);
      }

      y = x.minus(q.times(y));

      // To match JavaScript %, ensure sign of zero is sign of dividend.
      if (!y.c[0] && MODULO_MODE == 1) y.s = x.s;

      return y;
    };


    /*
     *  n * 0 = 0
     *  n * N = N
     *  n * I = I
     *  0 * n = 0
     *  0 * 0 = 0
     *  0 * N = N
     *  0 * I = N
     *  N * n = N
     *  N * 0 = N
     *  N * N = N
     *  N * I = N
     *  I * n = I
     *  I * 0 = N
     *  I * N = N
     *  I * I = I
     *
     * Return a new BigNumber whose value is the value of this BigNumber multiplied by the value
     * of BigNumber(y, b).
     */
    P.multipliedBy = P.times = function (y, b) {
      var c, e, i, j, k, m, xcL, xlo, xhi, ycL, ylo, yhi, zc,
        base, sqrtBase,
        x = this,
        xc = x.c,
        yc = (y = new BigNumber(y, b)).c;

      // Either NaN, ±Infinity or ±0?
      if (!xc || !yc || !xc[0] || !yc[0]) {

        // Return NaN if either is NaN, or one is 0 and the other is Infinity.
        if (!x.s || !y.s || xc && !xc[0] && !yc || yc && !yc[0] && !xc) {
          y.c = y.e = y.s = null;
        } else {
          y.s *= x.s;

          // Return ±Infinity if either is ±Infinity.
          if (!xc || !yc) {
            y.c = y.e = null;

          // Return ±0 if either is ±0.
          } else {
            y.c = [0];
            y.e = 0;
          }
        }

        return y;
      }

      e = bitFloor(x.e / LOG_BASE) + bitFloor(y.e / LOG_BASE);
      y.s *= x.s;
      xcL = xc.length;
      ycL = yc.length;

      // Ensure xc points to longer array and xcL to its length.
      if (xcL < ycL) {
        zc = xc;
        xc = yc;
        yc = zc;
        i = xcL;
        xcL = ycL;
        ycL = i;
      }

      // Initialise the result array with zeros.
      for (i = xcL + ycL, zc = []; i--; zc.push(0));

      base = BASE;
      sqrtBase = SQRT_BASE;

      for (i = ycL; --i >= 0;) {
        c = 0;
        ylo = yc[i] % sqrtBase;
        yhi = yc[i] / sqrtBase | 0;

        for (k = xcL, j = i + k; j > i;) {
          xlo = xc[--k] % sqrtBase;
          xhi = xc[k] / sqrtBase | 0;
          m = yhi * xlo + xhi * ylo;
          xlo = ylo * xlo + ((m % sqrtBase) * sqrtBase) + zc[j] + c;
          c = (xlo / base | 0) + (m / sqrtBase | 0) + yhi * xhi;
          zc[j--] = xlo % base;
        }

        zc[j] = c;
      }

      if (c) {
        ++e;
      } else {
        zc.splice(0, 1);
      }

      return normalise(y, zc, e);
    };


    /*
     * Return a new BigNumber whose value is the value of this BigNumber negated,
     * i.e. multiplied by -1.
     */
    P.negated = function () {
      var x = new BigNumber(this);
      x.s = -x.s || null;
      return x;
    };


    /*
     *  n + 0 = n
     *  n + N = N
     *  n + I = I
     *  0 + n = n
     *  0 + 0 = 0
     *  0 + N = N
     *  0 + I = I
     *  N + n = N
     *  N + 0 = N
     *  N + N = N
     *  N + I = N
     *  I + n = I
     *  I + 0 = I
     *  I + N = N
     *  I + I = I
     *
     * Return a new BigNumber whose value is the value of this BigNumber plus the value of
     * BigNumber(y, b).
     */
    P.plus = function (y, b) {
      var t,
        x = this,
        a = x.s;

      y = new BigNumber(y, b);
      b = y.s;

      // Either NaN?
      if (!a || !b) return new BigNumber(NaN);

      // Signs differ?
       if (a != b) {
        y.s = -b;
        return x.minus(y);
      }

      var xe = x.e / LOG_BASE,
        ye = y.e / LOG_BASE,
        xc = x.c,
        yc = y.c;

      if (!xe || !ye) {

        // Return ±Infinity if either ±Infinity.
        if (!xc || !yc) return new BigNumber(a / 0);

        // Either zero?
        // Return y if y is non-zero, x if x is non-zero, or zero if both are zero.
        if (!xc[0] || !yc[0]) return yc[0] ? y : new BigNumber(xc[0] ? x : a * 0);
      }

      xe = bitFloor(xe);
      ye = bitFloor(ye);
      xc = xc.slice();

      // Prepend zeros to equalise exponents. Faster to use reverse then do unshifts.
      if (a = xe - ye) {
        if (a > 0) {
          ye = xe;
          t = yc;
        } else {
          a = -a;
          t = xc;
        }

        t.reverse();
        for (; a--; t.push(0));
        t.reverse();
      }

      a = xc.length;
      b = yc.length;

      // Point xc to the longer array, and b to the shorter length.
      if (a - b < 0) {
        t = yc;
        yc = xc;
        xc = t;
        b = a;
      }

      // Only start adding at yc.length - 1 as the further digits of xc can be ignored.
      for (a = 0; b;) {
        a = (xc[--b] = xc[b] + yc[b] + a) / BASE | 0;
        xc[b] = BASE === xc[b] ? 0 : xc[b] % BASE;
      }

      if (a) {
        xc = [a].concat(xc);
        ++ye;
      }

      // No need to check for zero, as +x + +y != 0 && -x + -y != 0
      // ye = MAX_EXP + 1 possible
      return normalise(y, xc, ye);
    };


    /*
     * If sd is undefined or null or true or false, return the number of significant digits of
     * the value of this BigNumber, or null if the value of this BigNumber is ±Infinity or NaN.
     * If sd is true include integer-part trailing zeros in the count.
     *
     * Otherwise, if sd is a number, return a new BigNumber whose value is the value of this
     * BigNumber rounded to a maximum of sd significant digits using rounding mode rm, or
     * ROUNDING_MODE if rm is omitted.
     *
     * sd {number|boolean} number: significant digits: integer, 1 to MAX inclusive.
     *                     boolean: whether to count integer-part trailing zeros: true or false.
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {sd|rm}'
     */
    P.precision = P.sd = function (sd, rm) {
      var c, n, v,
        x = this;

      if (sd != null && sd !== !!sd) {
        intCheck(sd, 1, MAX);
        if (rm == null) rm = ROUNDING_MODE;
        else intCheck(rm, 0, 8);

        return round(new BigNumber(x), sd, rm);
      }

      if (!(c = x.c)) return null;
      v = c.length - 1;
      n = v * LOG_BASE + 1;

      if (v = c[v]) {

        // Subtract the number of trailing zeros of the last element.
        for (; v % 10 == 0; v /= 10, n--);

        // Add the number of digits of the first element.
        for (v = c[0]; v >= 10; v /= 10, n++);
      }

      if (sd && x.e + 1 > n) n = x.e + 1;

      return n;
    };


    /*
     * Return a new BigNumber whose value is the value of this BigNumber shifted by k places
     * (powers of 10). Shift to the right if n > 0, and to the left if n < 0.
     *
     * k {number} Integer, -MAX_SAFE_INTEGER to MAX_SAFE_INTEGER inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {k}'
     */
    P.shiftedBy = function (k) {
      intCheck(k, -MAX_SAFE_INTEGER, MAX_SAFE_INTEGER);
      return this.times('1e' + k);
    };


    /*
     *  sqrt(-n) =  N
     *  sqrt(N) =  N
     *  sqrt(-I) =  N
     *  sqrt(I) =  I
     *  sqrt(0) =  0
     *  sqrt(-0) = -0
     *
     * Return a new BigNumber whose value is the square root of the value of this BigNumber,
     * rounded according to DECIMAL_PLACES and ROUNDING_MODE.
     */
    P.squareRoot = P.sqrt = function () {
      var m, n, r, rep, t,
        x = this,
        c = x.c,
        s = x.s,
        e = x.e,
        dp = DECIMAL_PLACES + 4,
        half = new BigNumber('0.5');

      // Negative/NaN/Infinity/zero?
      if (s !== 1 || !c || !c[0]) {
        return new BigNumber(!s || s < 0 && (!c || c[0]) ? NaN : c ? x : 1 / 0);
      }

      // Initial estimate.
      s = Math.sqrt(+valueOf(x));

      // Math.sqrt underflow/overflow?
      // Pass x to Math.sqrt as integer, then adjust the exponent of the result.
      if (s == 0 || s == 1 / 0) {
        n = coeffToString(c);
        if ((n.length + e) % 2 == 0) n += '0';
        s = Math.sqrt(+n);
        e = bitFloor((e + 1) / 2) - (e < 0 || e % 2);

        if (s == 1 / 0) {
          n = '5e' + e;
        } else {
          n = s.toExponential();
          n = n.slice(0, n.indexOf('e') + 1) + e;
        }

        r = new BigNumber(n);
      } else {
        r = new BigNumber(s + '');
      }

      // Check for zero.
      // r could be zero if MIN_EXP is changed after the this value was created.
      // This would cause a division by zero (x/t) and hence Infinity below, which would cause
      // coeffToString to throw.
      if (r.c[0]) {
        e = r.e;
        s = e + dp;
        if (s < 3) s = 0;

        // Newton-Raphson iteration.
        for (; ;) {
          t = r;
          r = half.times(t.plus(div(x, t, dp, 1)));

          if (coeffToString(t.c).slice(0, s) === (n = coeffToString(r.c)).slice(0, s)) {

            // The exponent of r may here be one less than the final result exponent,
            // e.g 0.0009999 (e-4) --> 0.001 (e-3), so adjust s so the rounding digits
            // are indexed correctly.
            if (r.e < e) --s;
            n = n.slice(s - 3, s + 1);

            // The 4th rounding digit may be in error by -1 so if the 4 rounding digits
            // are 9999 or 4999 (i.e. approaching a rounding boundary) continue the
            // iteration.
            if (n == '9999' || !rep && n == '4999') {

              // On the first iteration only, check to see if rounding up gives the
              // exact result as the nines may infinitely repeat.
              if (!rep) {
                round(t, t.e + DECIMAL_PLACES + 2, 0);

                if (t.times(t).eq(x)) {
                  r = t;
                  break;
                }
              }

              dp += 4;
              s += 4;
              rep = 1;
            } else {

              // If rounding digits are null, 0{0,4} or 50{0,3}, check for exact
              // result. If not, then there are further digits and m will be truthy.
              if (!+n || !+n.slice(1) && n.charAt(0) == '5') {

                // Truncate to the first rounding digit.
                round(r, r.e + DECIMAL_PLACES + 2, 1);
                m = !r.times(r).eq(x);
              }

              break;
            }
          }
        }
      }

      return round(r, r.e + DECIMAL_PLACES + 1, ROUNDING_MODE, m);
    };


    /*
     * Return a string representing the value of this BigNumber in exponential notation and
     * rounded using ROUNDING_MODE to dp fixed decimal places.
     *
     * [dp] {number} Decimal places. Integer, 0 to MAX inclusive.
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {dp|rm}'
     */
    P.toExponential = function (dp, rm) {
      if (dp != null) {
        intCheck(dp, 0, MAX);
        dp++;
      }
      return format(this, dp, rm, 1);
    };


    /*
     * Return a string representing the value of this BigNumber in fixed-point notation rounding
     * to dp fixed decimal places using rounding mode rm, or ROUNDING_MODE if rm is omitted.
     *
     * Note: as with JavaScript's number type, (-0).toFixed(0) is '0',
     * but e.g. (-0.00001).toFixed(0) is '-0'.
     *
     * [dp] {number} Decimal places. Integer, 0 to MAX inclusive.
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {dp|rm}'
     */
    P.toFixed = function (dp, rm) {
      if (dp != null) {
        intCheck(dp, 0, MAX);
        dp = dp + this.e + 1;
      }
      return format(this, dp, rm);
    };


    /*
     * Return a string representing the value of this BigNumber in fixed-point notation rounded
     * using rm or ROUNDING_MODE to dp decimal places, and formatted according to the properties
     * of the format or FORMAT object (see BigNumber.set).
     *
     * The formatting object may contain some or all of the properties shown below.
     *
     * FORMAT = {
     *   prefix: '',
     *   groupSize: 3,
     *   secondaryGroupSize: 0,
     *   groupSeparator: ',',
     *   decimalSeparator: '.',
     *   fractionGroupSize: 0,
     *   fractionGroupSeparator: '\xA0',      // non-breaking space
     *   suffix: ''
     * };
     *
     * [dp] {number} Decimal places. Integer, 0 to MAX inclusive.
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     * [format] {object} Formatting options. See FORMAT pbject above.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {dp|rm}'
     * '[BigNumber Error] Argument not an object: {format}'
     */
    P.toFormat = function (dp, rm, format) {
      var str,
        x = this;

      if (format == null) {
        if (dp != null && rm && typeof rm == 'object') {
          format = rm;
          rm = null;
        } else if (dp && typeof dp == 'object') {
          format = dp;
          dp = rm = null;
        } else {
          format = FORMAT;
        }
      } else if (typeof format != 'object') {
        throw Error
          (bignumberError + 'Argument not an object: ' + format);
      }

      str = x.toFixed(dp, rm);

      if (x.c) {
        var i,
          arr = str.split('.'),
          g1 = +format.groupSize,
          g2 = +format.secondaryGroupSize,
          groupSeparator = format.groupSeparator || '',
          intPart = arr[0],
          fractionPart = arr[1],
          isNeg = x.s < 0,
          intDigits = isNeg ? intPart.slice(1) : intPart,
          len = intDigits.length;

        if (g2) {
          i = g1;
          g1 = g2;
          g2 = i;
          len -= i;
        }

        if (g1 > 0 && len > 0) {
          i = len % g1 || g1;
          intPart = intDigits.substr(0, i);
          for (; i < len; i += g1) intPart += groupSeparator + intDigits.substr(i, g1);
          if (g2 > 0) intPart += groupSeparator + intDigits.slice(i);
          if (isNeg) intPart = '-' + intPart;
        }

        str = fractionPart
         ? intPart + (format.decimalSeparator || '') + ((g2 = +format.fractionGroupSize)
          ? fractionPart.replace(new RegExp('\\d{' + g2 + '}\\B', 'g'),
           '$&' + (format.fractionGroupSeparator || ''))
          : fractionPart)
         : intPart;
      }

      return (format.prefix || '') + str + (format.suffix || '');
    };


    /*
     * Return an array of two BigNumbers representing the value of this BigNumber as a simple
     * fraction with an integer numerator and an integer denominator.
     * The denominator will be a positive non-zero value less than or equal to the specified
     * maximum denominator. If a maximum denominator is not specified, the denominator will be
     * the lowest value necessary to represent the number exactly.
     *
     * [md] {number|string|BigNumber} Integer >= 1, or Infinity. The maximum denominator.
     *
     * '[BigNumber Error] Argument {not an integer|out of range} : {md}'
     */
    P.toFraction = function (md) {
      var d, d0, d1, d2, e, exp, n, n0, n1, q, r, s,
        x = this,
        xc = x.c;

      if (md != null) {
        n = new BigNumber(md);

        // Throw if md is less than one or is not an integer, unless it is Infinity.
        if (!n.isInteger() && (n.c || n.s !== 1) || n.lt(ONE)) {
          throw Error
            (bignumberError + 'Argument ' +
              (n.isInteger() ? 'out of range: ' : 'not an integer: ') + valueOf(n));
        }
      }

      if (!xc) return new BigNumber(x);

      d = new BigNumber(ONE);
      n1 = d0 = new BigNumber(ONE);
      d1 = n0 = new BigNumber(ONE);
      s = coeffToString(xc);

      // Determine initial denominator.
      // d is a power of 10 and the minimum max denominator that specifies the value exactly.
      e = d.e = s.length - x.e - 1;
      d.c[0] = POWS_TEN[(exp = e % LOG_BASE) < 0 ? LOG_BASE + exp : exp];
      md = !md || n.comparedTo(d) > 0 ? (e > 0 ? d : n1) : n;

      exp = MAX_EXP;
      MAX_EXP = 1 / 0;
      n = new BigNumber(s);

      // n0 = d1 = 0
      n0.c[0] = 0;

      for (; ;)  {
        q = div(n, d, 0, 1);
        d2 = d0.plus(q.times(d1));
        if (d2.comparedTo(md) == 1) break;
        d0 = d1;
        d1 = d2;
        n1 = n0.plus(q.times(d2 = n1));
        n0 = d2;
        d = n.minus(q.times(d2 = d));
        n = d2;
      }

      d2 = div(md.minus(d0), d1, 0, 1);
      n0 = n0.plus(d2.times(n1));
      d0 = d0.plus(d2.times(d1));
      n0.s = n1.s = x.s;
      e = e * 2;

      // Determine which fraction is closer to x, n0/d0 or n1/d1
      r = div(n1, d1, e, ROUNDING_MODE).minus(x).abs().comparedTo(
          div(n0, d0, e, ROUNDING_MODE).minus(x).abs()) < 1 ? [n1, d1] : [n0, d0];

      MAX_EXP = exp;

      return r;
    };


    /*
     * Return the value of this BigNumber converted to a number primitive.
     */
    P.toNumber = function () {
      return +valueOf(this);
    };


    /*
     * Return a string representing the value of this BigNumber rounded to sd significant digits
     * using rounding mode rm or ROUNDING_MODE. If sd is less than the number of digits
     * necessary to represent the integer part of the value in fixed-point notation, then use
     * exponential notation.
     *
     * [sd] {number} Significant digits. Integer, 1 to MAX inclusive.
     * [rm] {number} Rounding mode. Integer, 0 to 8 inclusive.
     *
     * '[BigNumber Error] Argument {not a primitive number|not an integer|out of range}: {sd|rm}'
     */
    P.toPrecision = function (sd, rm) {
      if (sd != null) intCheck(sd, 1, MAX);
      return format(this, sd, rm, 2);
    };


    /*
     * Return a string representing the value of this BigNumber in base b, or base 10 if b is
     * omitted. If a base is specified, including base 10, round according to DECIMAL_PLACES and
     * ROUNDING_MODE. If a base is not specified, and this BigNumber has a positive exponent
     * that is equal to or greater than TO_EXP_POS, or a negative exponent equal to or less than
     * TO_EXP_NEG, return exponential notation.
     *
     * [b] {number} Integer, 2 to ALPHABET.length inclusive.
     *
     * '[BigNumber Error] Base {not a primitive number|not an integer|out of range}: {b}'
     */
    P.toString = function (b) {
      var str,
        n = this,
        s = n.s,
        e = n.e;

      // Infinity or NaN?
      if (e === null) {
        if (s) {
          str = 'Infinity';
          if (s < 0) str = '-' + str;
        } else {
          str = 'NaN';
        }
      } else {
        if (b == null) {
          str = e <= TO_EXP_NEG || e >= TO_EXP_POS
           ? toExponential(coeffToString(n.c), e)
           : toFixedPoint(coeffToString(n.c), e, '0');
        } else if (b === 10 && alphabetHasNormalDecimalDigits) {
          n = round(new BigNumber(n), DECIMAL_PLACES + e + 1, ROUNDING_MODE);
          str = toFixedPoint(coeffToString(n.c), n.e, '0');
        } else {
          intCheck(b, 2, ALPHABET.length, 'Base');
          str = convertBase(toFixedPoint(coeffToString(n.c), e, '0'), 10, b, s, true);
        }

        if (s < 0 && n.c[0]) str = '-' + str;
      }

      return str;
    };


    /*
     * Return as toString, but do not accept a base argument, and include the minus sign for
     * negative zero.
     */
    P.valueOf = P.toJSON = function () {
      return valueOf(this);
    };


    P._isBigNumber = true;

    if (configObject != null) BigNumber.set(configObject);

    return BigNumber;
  }


  // PRIVATE HELPER FUNCTIONS

  // These functions don't need access to variables,
  // e.g. DECIMAL_PLACES, in the scope of the `clone` function above.


  function bitFloor(n) {
    var i = n | 0;
    return n > 0 || n === i ? i : i - 1;
  }


  // Return a coefficient array as a string of base 10 digits.
  function coeffToString(a) {
    var s, z,
      i = 1,
      j = a.length,
      r = a[0] + '';

    for (; i < j;) {
      s = a[i++] + '';
      z = LOG_BASE - s.length;
      for (; z--; s = '0' + s);
      r += s;
    }

    // Determine trailing zeros.
    for (j = r.length; r.charCodeAt(--j) === 48;);

    return r.slice(0, j + 1 || 1);
  }


  // Compare the value of BigNumbers x and y.
  function compare(x, y) {
    var a, b,
      xc = x.c,
      yc = y.c,
      i = x.s,
      j = y.s,
      k = x.e,
      l = y.e;

    // Either NaN?
    if (!i || !j) return null;

    a = xc && !xc[0];
    b = yc && !yc[0];

    // Either zero?
    if (a || b) return a ? b ? 0 : -j : i;

    // Signs differ?
    if (i != j) return i;

    a = i < 0;
    b = k == l;

    // Either Infinity?
    if (!xc || !yc) return b ? 0 : !xc ^ a ? 1 : -1;

    // Compare exponents.
    if (!b) return k > l ^ a ? 1 : -1;

    j = (k = xc.length) < (l = yc.length) ? k : l;

    // Compare digit by digit.
    for (i = 0; i < j; i++) if (xc[i] != yc[i]) return xc[i] > yc[i] ^ a ? 1 : -1;

    // Compare lengths.
    return k == l ? 0 : k > l ^ a ? 1 : -1;
  }


  /*
   * Check that n is a primitive number, an integer, and in range, otherwise throw.
   */
  function intCheck(n, min, max, name) {
    if (n < min || n > max || n !== mathfloor(n)) {
      throw Error
       (bignumberError + (name || 'Argument') + (typeof n == 'number'
         ? n < min || n > max ? ' out of range: ' : ' not an integer: '
         : ' not a primitive number: ') + String(n));
    }
  }


  // Assumes finite n.
  function isOdd(n) {
    var k = n.c.length - 1;
    return bitFloor(n.e / LOG_BASE) == k && n.c[k] % 2 != 0;
  }


  function toExponential(str, e) {
    return (str.length > 1 ? str.charAt(0) + '.' + str.slice(1) : str) +
     (e < 0 ? 'e' : 'e+') + e;
  }


  function toFixedPoint(str, e, z) {
    var len, zs;

    // Negative exponent?
    if (e < 0) {

      // Prepend zeros.
      for (zs = z + '.'; ++e; zs += z);
      str = zs + str;

    // Positive exponent
    } else {
      len = str.length;

      // Append zeros.
      if (++e > len) {
        for (zs = z, e -= len; --e; zs += z);
        str += zs;
      } else if (e < len) {
        str = str.slice(0, e) + '.' + str.slice(e);
      }
    }

    return str;
  }


  // EXPORT


  BigNumber = clone();
  BigNumber['default'] = BigNumber.BigNumber = BigNumber;

  // AMD.
  if (true) {
    !(__WEBPACK_AMD_DEFINE_RESULT__ = (function () { return BigNumber; }).call(exports, __webpack_require__, exports, module),
		__WEBPACK_AMD_DEFINE_RESULT__ !== undefined && (module.exports = __WEBPACK_AMD_DEFINE_RESULT__));

  // Node.js and other environments that support module.exports.
  } else {}
})(this);


/***/ }),

/***/ "../../node_modules/eventemitter3/index.js":
/*!*************************************************!*\
  !*** ../../node_modules/eventemitter3/index.js ***!
  \*************************************************/
/***/ ((module) => {

"use strict";


var has = Object.prototype.hasOwnProperty
  , prefix = '~';

/**
 * Constructor to create a storage for our `EE` objects.
 * An `Events` instance is a plain object whose properties are event names.
 *
 * @constructor
 * @private
 */
function Events() {}

//
// We try to not inherit from `Object.prototype`. In some engines creating an
// instance in this way is faster than calling `Object.create(null)` directly.
// If `Object.create(null)` is not supported we prefix the event names with a
// character to make sure that the built-in object properties are not
// overridden or used as an attack vector.
//
if (Object.create) {
  Events.prototype = Object.create(null);

  //
  // This hack is needed because the `__proto__` property is still inherited in
  // some old browsers like Android 4, iPhone 5.1, Opera 11 and Safari 5.
  //
  if (!new Events().__proto__) prefix = false;
}

/**
 * Representation of a single event listener.
 *
 * @param {Function} fn The listener function.
 * @param {*} context The context to invoke the listener with.
 * @param {Boolean} [once=false] Specify if the listener is a one-time listener.
 * @constructor
 * @private
 */
function EE(fn, context, once) {
  this.fn = fn;
  this.context = context;
  this.once = once || false;
}

/**
 * Add a listener for a given event.
 *
 * @param {EventEmitter} emitter Reference to the `EventEmitter` instance.
 * @param {(String|Symbol)} event The event name.
 * @param {Function} fn The listener function.
 * @param {*} context The context to invoke the listener with.
 * @param {Boolean} once Specify if the listener is a one-time listener.
 * @returns {EventEmitter}
 * @private
 */
function addListener(emitter, event, fn, context, once) {
  if (typeof fn !== 'function') {
    throw new TypeError('The listener must be a function');
  }

  var listener = new EE(fn, context || emitter, once)
    , evt = prefix ? prefix + event : event;

  if (!emitter._events[evt]) emitter._events[evt] = listener, emitter._eventsCount++;
  else if (!emitter._events[evt].fn) emitter._events[evt].push(listener);
  else emitter._events[evt] = [emitter._events[evt], listener];

  return emitter;
}

/**
 * Clear event by name.
 *
 * @param {EventEmitter} emitter Reference to the `EventEmitter` instance.
 * @param {(String|Symbol)} evt The Event name.
 * @private
 */
function clearEvent(emitter, evt) {
  if (--emitter._eventsCount === 0) emitter._events = new Events();
  else delete emitter._events[evt];
}

/**
 * Minimal `EventEmitter` interface that is molded against the Node.js
 * `EventEmitter` interface.
 *
 * @constructor
 * @public
 */
function EventEmitter() {
  this._events = new Events();
  this._eventsCount = 0;
}

/**
 * Return an array listing the events for which the emitter has registered
 * listeners.
 *
 * @returns {Array}
 * @public
 */
EventEmitter.prototype.eventNames = function eventNames() {
  var names = []
    , events
    , name;

  if (this._eventsCount === 0) return names;

  for (name in (events = this._events)) {
    if (has.call(events, name)) names.push(prefix ? name.slice(1) : name);
  }

  if (Object.getOwnPropertySymbols) {
    return names.concat(Object.getOwnPropertySymbols(events));
  }

  return names;
};

/**
 * Return the listeners registered for a given event.
 *
 * @param {(String|Symbol)} event The event name.
 * @returns {Array} The registered listeners.
 * @public
 */
EventEmitter.prototype.listeners = function listeners(event) {
  var evt = prefix ? prefix + event : event
    , handlers = this._events[evt];

  if (!handlers) return [];
  if (handlers.fn) return [handlers.fn];

  for (var i = 0, l = handlers.length, ee = new Array(l); i < l; i++) {
    ee[i] = handlers[i].fn;
  }

  return ee;
};

/**
 * Return the number of listeners listening to a given event.
 *
 * @param {(String|Symbol)} event The event name.
 * @returns {Number} The number of listeners.
 * @public
 */
EventEmitter.prototype.listenerCount = function listenerCount(event) {
  var evt = prefix ? prefix + event : event
    , listeners = this._events[evt];

  if (!listeners) return 0;
  if (listeners.fn) return 1;
  return listeners.length;
};

/**
 * Calls each of the listeners registered for a given event.
 *
 * @param {(String|Symbol)} event The event name.
 * @returns {Boolean} `true` if the event had listeners, else `false`.
 * @public
 */
EventEmitter.prototype.emit = function emit(event, a1, a2, a3, a4, a5) {
  var evt = prefix ? prefix + event : event;

  if (!this._events[evt]) return false;

  var listeners = this._events[evt]
    , len = arguments.length
    , args
    , i;

  if (listeners.fn) {
    if (listeners.once) this.removeListener(event, listeners.fn, undefined, true);

    switch (len) {
      case 1: return listeners.fn.call(listeners.context), true;
      case 2: return listeners.fn.call(listeners.context, a1), true;
      case 3: return listeners.fn.call(listeners.context, a1, a2), true;
      case 4: return listeners.fn.call(listeners.context, a1, a2, a3), true;
      case 5: return listeners.fn.call(listeners.context, a1, a2, a3, a4), true;
      case 6: return listeners.fn.call(listeners.context, a1, a2, a3, a4, a5), true;
    }

    for (i = 1, args = new Array(len -1); i < len; i++) {
      args[i - 1] = arguments[i];
    }

    listeners.fn.apply(listeners.context, args);
  } else {
    var length = listeners.length
      , j;

    for (i = 0; i < length; i++) {
      if (listeners[i].once) this.removeListener(event, listeners[i].fn, undefined, true);

      switch (len) {
        case 1: listeners[i].fn.call(listeners[i].context); break;
        case 2: listeners[i].fn.call(listeners[i].context, a1); break;
        case 3: listeners[i].fn.call(listeners[i].context, a1, a2); break;
        case 4: listeners[i].fn.call(listeners[i].context, a1, a2, a3); break;
        default:
          if (!args) for (j = 1, args = new Array(len -1); j < len; j++) {
            args[j - 1] = arguments[j];
          }

          listeners[i].fn.apply(listeners[i].context, args);
      }
    }
  }

  return true;
};

/**
 * Add a listener for a given event.
 *
 * @param {(String|Symbol)} event The event name.
 * @param {Function} fn The listener function.
 * @param {*} [context=this] The context to invoke the listener with.
 * @returns {EventEmitter} `this`.
 * @public
 */
EventEmitter.prototype.on = function on(event, fn, context) {
  return addListener(this, event, fn, context, false);
};

/**
 * Add a one-time listener for a given event.
 *
 * @param {(String|Symbol)} event The event name.
 * @param {Function} fn The listener function.
 * @param {*} [context=this] The context to invoke the listener with.
 * @returns {EventEmitter} `this`.
 * @public
 */
EventEmitter.prototype.once = function once(event, fn, context) {
  return addListener(this, event, fn, context, true);
};

/**
 * Remove the listeners of a given event.
 *
 * @param {(String|Symbol)} event The event name.
 * @param {Function} fn Only remove the listeners that match this function.
 * @param {*} context Only remove the listeners that have this context.
 * @param {Boolean} once Only remove one-time listeners.
 * @returns {EventEmitter} `this`.
 * @public
 */
EventEmitter.prototype.removeListener = function removeListener(event, fn, context, once) {
  var evt = prefix ? prefix + event : event;

  if (!this._events[evt]) return this;
  if (!fn) {
    clearEvent(this, evt);
    return this;
  }

  var listeners = this._events[evt];

  if (listeners.fn) {
    if (
      listeners.fn === fn &&
      (!once || listeners.once) &&
      (!context || listeners.context === context)
    ) {
      clearEvent(this, evt);
    }
  } else {
    for (var i = 0, events = [], length = listeners.length; i < length; i++) {
      if (
        listeners[i].fn !== fn ||
        (once && !listeners[i].once) ||
        (context && listeners[i].context !== context)
      ) {
        events.push(listeners[i]);
      }
    }

    //
    // Reset the array, or remove it completely if we have no more listeners.
    //
    if (events.length) this._events[evt] = events.length === 1 ? events[0] : events;
    else clearEvent(this, evt);
  }

  return this;
};

/**
 * Remove all listeners, or those of the specified event.
 *
 * @param {(String|Symbol)} [event] The event name.
 * @returns {EventEmitter} `this`.
 * @public
 */
EventEmitter.prototype.removeAllListeners = function removeAllListeners(event) {
  var evt;

  if (event) {
    evt = prefix ? prefix + event : event;
    if (this._events[evt]) clearEvent(this, evt);
  } else {
    this._events = new Events();
    this._eventsCount = 0;
  }

  return this;
};

//
// Alias methods names because people roll like that.
//
EventEmitter.prototype.off = EventEmitter.prototype.removeListener;
EventEmitter.prototype.addListener = EventEmitter.prototype.on;

//
// Expose the prefix.
//
EventEmitter.prefixed = prefix;

//
// Allow `EventEmitter` to be imported as module namespace.
//
EventEmitter.EventEmitter = EventEmitter;

//
// Expose the module.
//
if (true) {
  module.exports = EventEmitter;
}


/***/ }),

/***/ "../../node_modules/ripple-address-codec/dist/index.js":
/*!*************************************************************!*\
  !*** ../../node_modules/ripple-address-codec/dist/index.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.isValidXAddress = exports.decodeXAddress = exports.xAddressToClassicAddress = exports.encodeXAddress = exports.classicAddressToXAddress = exports.isValidClassicAddress = exports.decodeAccountPublic = exports.encodeAccountPublic = exports.decodeNodePublic = exports.encodeNodePublic = exports.decodeAccountID = exports.encodeAccountID = exports.decodeSeed = exports.encodeSeed = exports.codec = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const xrp_codec_1 = __webpack_require__(/*! ./xrp-codec */ "../../node_modules/ripple-address-codec/dist/xrp-codec.js");
Object.defineProperty(exports, "codec", ({ enumerable: true, get: function () { return xrp_codec_1.codec; } }));
Object.defineProperty(exports, "encodeSeed", ({ enumerable: true, get: function () { return xrp_codec_1.encodeSeed; } }));
Object.defineProperty(exports, "decodeSeed", ({ enumerable: true, get: function () { return xrp_codec_1.decodeSeed; } }));
Object.defineProperty(exports, "encodeAccountID", ({ enumerable: true, get: function () { return xrp_codec_1.encodeAccountID; } }));
Object.defineProperty(exports, "decodeAccountID", ({ enumerable: true, get: function () { return xrp_codec_1.decodeAccountID; } }));
Object.defineProperty(exports, "encodeNodePublic", ({ enumerable: true, get: function () { return xrp_codec_1.encodeNodePublic; } }));
Object.defineProperty(exports, "decodeNodePublic", ({ enumerable: true, get: function () { return xrp_codec_1.decodeNodePublic; } }));
Object.defineProperty(exports, "encodeAccountPublic", ({ enumerable: true, get: function () { return xrp_codec_1.encodeAccountPublic; } }));
Object.defineProperty(exports, "decodeAccountPublic", ({ enumerable: true, get: function () { return xrp_codec_1.decodeAccountPublic; } }));
Object.defineProperty(exports, "isValidClassicAddress", ({ enumerable: true, get: function () { return xrp_codec_1.isValidClassicAddress; } }));
const PREFIX_BYTES = {
    // 5, 68
    main: Uint8Array.from([0x05, 0x44]),
    // 4, 147
    test: Uint8Array.from([0x04, 0x93]),
};
const MAX_32_BIT_UNSIGNED_INT = 4294967295;
function classicAddressToXAddress(classicAddress, tag, test) {
    const accountId = (0, xrp_codec_1.decodeAccountID)(classicAddress);
    return encodeXAddress(accountId, tag, test);
}
exports.classicAddressToXAddress = classicAddressToXAddress;
function encodeXAddress(accountId, tag, test) {
    if (accountId.length !== 20) {
        // RIPEMD160 is 160 bits = 20 bytes
        throw new Error('Account ID must be 20 bytes');
    }
    if (tag !== false && tag > MAX_32_BIT_UNSIGNED_INT) {
        throw new Error('Invalid tag');
    }
    const theTag = tag || 0;
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- Passing null is a common js mistake
    const flag = tag === false || tag == null ? 0 : 1;
    /* eslint-disable no-bitwise ---
     * need to use bitwise operations here */
    const bytes = (0, utils_1.concat)([
        test ? PREFIX_BYTES.test : PREFIX_BYTES.main,
        accountId,
        Uint8Array.from([
            // 0x00 if no tag, 0x01 if 32-bit tag
            flag,
            // first byte
            theTag & 0xff,
            // second byte
            (theTag >> 8) & 0xff,
            // third byte
            (theTag >> 16) & 0xff,
            // fourth byte
            (theTag >> 24) & 0xff,
            0,
            0,
            0,
            // four zero bytes (reserved for 64-bit tags)
            0,
        ]),
    ]);
    /* eslint-enable no-bitwise */
    return xrp_codec_1.codec.encodeChecked(bytes);
}
exports.encodeXAddress = encodeXAddress;
function xAddressToClassicAddress(xAddress) {
    /* eslint-disable @typescript-eslint/naming-convention --
     * TODO 'test' should be something like 'isTest', do this later
     */
    const { accountId, tag, test } = decodeXAddress(xAddress);
    /* eslint-enable @typescript-eslint/naming-convention */
    const classicAddress = (0, xrp_codec_1.encodeAccountID)(accountId);
    return {
        classicAddress,
        tag,
        test,
    };
}
exports.xAddressToClassicAddress = xAddressToClassicAddress;
function decodeXAddress(xAddress) {
    const decoded = xrp_codec_1.codec.decodeChecked(xAddress);
    /* eslint-disable @typescript-eslint/naming-convention --
     * TODO 'test' should be something like 'isTest', do this later
     */
    const test = isUint8ArrayForTestAddress(decoded);
    /* eslint-enable @typescript-eslint/naming-convention */
    const accountId = decoded.slice(2, 22);
    const tag = tagFromUint8Array(decoded);
    return {
        accountId,
        tag,
        test,
    };
}
exports.decodeXAddress = decodeXAddress;
function isUint8ArrayForTestAddress(buf) {
    const decodedPrefix = buf.slice(0, 2);
    if ((0, utils_1.equal)(PREFIX_BYTES.main, decodedPrefix)) {
        return false;
    }
    if ((0, utils_1.equal)(PREFIX_BYTES.test, decodedPrefix)) {
        return true;
    }
    throw new Error('Invalid X-address: bad prefix');
}
function tagFromUint8Array(buf) {
    const flag = buf[22];
    if (flag >= 2) {
        // No support for 64-bit tags at this time
        throw new Error('Unsupported X-address');
    }
    if (flag === 1) {
        // Little-endian to big-endian
        return buf[23] + buf[24] * 0x100 + buf[25] * 0x10000 + buf[26] * 0x1000000;
    }
    if (flag !== 0) {
        throw new Error('flag must be zero to indicate no tag');
    }
    if (!(0, utils_1.equal)((0, utils_1.hexToBytes)('0000000000000000'), buf.slice(23, 23 + 8))) {
        throw new Error('remaining bytes must be zero');
    }
    return false;
}
function isValidXAddress(xAddress) {
    try {
        decodeXAddress(xAddress);
    }
    catch (_error) {
        return false;
    }
    return true;
}
exports.isValidXAddress = isValidXAddress;


/***/ }),

/***/ "../../node_modules/ripple-address-codec/dist/utils.js":
/*!*************************************************************!*\
  !*** ../../node_modules/ripple-address-codec/dist/utils.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.concatArgs = exports.arrayEqual = void 0;
/**
 * Check whether two sequences (e.g. Arrays of numbers) are equal.
 *
 * @param arr1 - One of the arrays to compare.
 * @param arr2 - The other array to compare.
 */
function arrayEqual(arr1, arr2) {
    if (arr1.length !== arr2.length) {
        return false;
    }
    return arr1.every((value, index) => value === arr2[index]);
}
exports.arrayEqual = arrayEqual;
/**
 * Check whether a value is a scalar
 *
 * @param val - The value to check.
 */
function isScalar(val) {
    return typeof val === 'number';
}
/**
 * Concatenate all `arguments` into a single array. Each argument can be either
 * a single element or a sequence, which has a `length` property and supports
 * element retrieval via sequence[ix].
 *
 * > concatArgs(1, [2, 3], Uint8Array.from([4,5]), new Uint8Array([6, 7]));
 * [1,2,3,4,5,6,7]
 *
 * @param args - Concatenate of these args into a single array.
 * @returns Array of concatenated arguments
 */
function concatArgs(...args) {
    return args.flatMap((arg) => {
        return isScalar(arg) ? [arg] : Array.from(arg);
    });
}
exports.concatArgs = concatArgs;


/***/ }),

/***/ "../../node_modules/ripple-address-codec/dist/xrp-codec.js":
/*!*****************************************************************!*\
  !*** ../../node_modules/ripple-address-codec/dist/xrp-codec.js ***!
  \*****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

/**
 * Codec class
 */
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.isValidClassicAddress = exports.decodeAccountPublic = exports.encodeAccountPublic = exports.encodeNodePublic = exports.decodeNodePublic = exports.decodeAddress = exports.decodeAccountID = exports.encodeAddress = exports.encodeAccountID = exports.decodeSeed = exports.encodeSeed = exports.codec = void 0;
const base_1 = __webpack_require__(/*! @scure/base */ "../../node_modules/@scure/base/lib/index.js");
const sha256_1 = __webpack_require__(/*! @xrplf/isomorphic/sha256 */ "../../node_modules/@xrplf/isomorphic/dist/sha256/browser.js");
const utils_1 = __webpack_require__(/*! ./utils */ "../../node_modules/ripple-address-codec/dist/utils.js");
class Codec {
    constructor(options) {
        this._sha256 = options.sha256;
        this._codec = base_1.base58xrp;
    }
    /**
     * Encoder.
     *
     * @param bytes - Uint8Array of data to encode.
     * @param opts - Options object including the version bytes and the expected length of the data to encode.
     */
    encode(bytes, opts) {
        const versions = opts.versions;
        return this._encodeVersioned(bytes, versions, opts.expectedLength);
    }
    /**
     * Decoder.
     *
     * @param base58string - Base58Check-encoded string to decode.
     * @param opts - Options object including the version byte(s) and the expected length of the data after decoding.
     */
    /* eslint-disable max-lines-per-function --
     * TODO refactor */
    decode(base58string, opts) {
        var _a;
        const versions = opts.versions;
        const types = opts.versionTypes;
        const withoutSum = this.decodeChecked(base58string);
        if (versions.length > 1 && !opts.expectedLength) {
            throw new Error('expectedLength is required because there are >= 2 possible versions');
        }
        const versionLengthGuess = typeof versions[0] === 'number' ? 1 : versions[0].length;
        const payloadLength = (_a = opts.expectedLength) !== null && _a !== void 0 ? _a : withoutSum.length - versionLengthGuess;
        const versionBytes = withoutSum.slice(0, -payloadLength);
        const payload = withoutSum.slice(-payloadLength);
        for (let i = 0; i < versions.length; i++) {
            /* eslint-disable @typescript-eslint/consistent-type-assertions --
             * TODO refactor */
            const version = Array.isArray(versions[i])
                ? versions[i]
                : [versions[i]];
            if ((0, utils_1.arrayEqual)(versionBytes, version)) {
                return {
                    version,
                    bytes: payload,
                    type: types ? types[i] : null,
                };
            }
            /* eslint-enable @typescript-eslint/consistent-type-assertions */
        }
        throw new Error('version_invalid: version bytes do not match any of the provided version(s)');
    }
    encodeChecked(bytes) {
        const check = this._sha256(this._sha256(bytes)).slice(0, 4);
        return this._encodeRaw(Uint8Array.from((0, utils_1.concatArgs)(bytes, check)));
    }
    decodeChecked(base58string) {
        const intArray = this._decodeRaw(base58string);
        if (intArray.byteLength < 5) {
            throw new Error('invalid_input_size: decoded data must have length >= 5');
        }
        if (!this._verifyCheckSum(intArray)) {
            throw new Error('checksum_invalid');
        }
        return intArray.slice(0, -4);
    }
    _encodeVersioned(bytes, versions, expectedLength) {
        if (!checkByteLength(bytes, expectedLength)) {
            throw new Error('unexpected_payload_length: bytes.length does not match expectedLength.' +
                ' Ensure that the bytes are a Uint8Array.');
        }
        return this.encodeChecked((0, utils_1.concatArgs)(versions, bytes));
    }
    _encodeRaw(bytes) {
        return this._codec.encode(Uint8Array.from(bytes));
    }
    /* eslint-enable max-lines-per-function */
    _decodeRaw(base58string) {
        return this._codec.decode(base58string);
    }
    _verifyCheckSum(bytes) {
        const computed = this._sha256(this._sha256(bytes.slice(0, -4))).slice(0, 4);
        const checksum = bytes.slice(-4);
        return (0, utils_1.arrayEqual)(computed, checksum);
    }
}
/**
 * XRP codec
 */
// base58 encodings: https://xrpl.org/base58-encodings.html
// Account address (20 bytes)
const ACCOUNT_ID = 0;
// Account public key (33 bytes)
const ACCOUNT_PUBLIC_KEY = 0x23;
// 33; Seed value (for secret keys) (16 bytes)
const FAMILY_SEED = 0x21;
// 28; Validation public key (33 bytes)
const NODE_PUBLIC = 0x1c;
// [1, 225, 75]
const ED25519_SEED = [0x01, 0xe1, 0x4b];
const codecOptions = {
    sha256: sha256_1.sha256,
};
const codecWithXrpAlphabet = new Codec(codecOptions);
exports.codec = codecWithXrpAlphabet;
// entropy is a Uint8Array of size 16
// type is 'ed25519' or 'secp256k1'
function encodeSeed(entropy, type) {
    if (!checkByteLength(entropy, 16)) {
        throw new Error('entropy must have length 16');
    }
    const opts = {
        expectedLength: 16,
        // for secp256k1, use `FAMILY_SEED`
        versions: type === 'ed25519' ? ED25519_SEED : [FAMILY_SEED],
    };
    // prefixes entropy with version bytes
    return codecWithXrpAlphabet.encode(entropy, opts);
}
exports.encodeSeed = encodeSeed;
function decodeSeed(seed, opts = {
    versionTypes: ['ed25519', 'secp256k1'],
    versions: [ED25519_SEED, FAMILY_SEED],
    expectedLength: 16,
}) {
    return codecWithXrpAlphabet.decode(seed, opts);
}
exports.decodeSeed = decodeSeed;
function encodeAccountID(bytes) {
    const opts = { versions: [ACCOUNT_ID], expectedLength: 20 };
    return codecWithXrpAlphabet.encode(bytes, opts);
}
exports.encodeAccountID = encodeAccountID;
/* eslint-disable import/no-unused-modules ---
 * unclear why this is aliased but we should keep it in case someone else is
 * importing it with the aliased name */
exports.encodeAddress = encodeAccountID;
/* eslint-enable import/no-unused-modules */
function decodeAccountID(accountId) {
    const opts = { versions: [ACCOUNT_ID], expectedLength: 20 };
    return codecWithXrpAlphabet.decode(accountId, opts).bytes;
}
exports.decodeAccountID = decodeAccountID;
/* eslint-disable import/no-unused-modules ---
 * unclear why this is aliased but we should keep it in case someone else is
 * importing it with the aliased name */
exports.decodeAddress = decodeAccountID;
/* eslint-enable import/no-unused-modules */
function decodeNodePublic(base58string) {
    const opts = { versions: [NODE_PUBLIC], expectedLength: 33 };
    return codecWithXrpAlphabet.decode(base58string, opts).bytes;
}
exports.decodeNodePublic = decodeNodePublic;
function encodeNodePublic(bytes) {
    const opts = { versions: [NODE_PUBLIC], expectedLength: 33 };
    return codecWithXrpAlphabet.encode(bytes, opts);
}
exports.encodeNodePublic = encodeNodePublic;
function encodeAccountPublic(bytes) {
    const opts = { versions: [ACCOUNT_PUBLIC_KEY], expectedLength: 33 };
    return codecWithXrpAlphabet.encode(bytes, opts);
}
exports.encodeAccountPublic = encodeAccountPublic;
function decodeAccountPublic(base58string) {
    const opts = { versions: [ACCOUNT_PUBLIC_KEY], expectedLength: 33 };
    return codecWithXrpAlphabet.decode(base58string, opts).bytes;
}
exports.decodeAccountPublic = decodeAccountPublic;
function isValidClassicAddress(address) {
    try {
        decodeAccountID(address);
    }
    catch (_error) {
        return false;
    }
    return true;
}
exports.isValidClassicAddress = isValidClassicAddress;
function checkByteLength(bytes, expectedLength) {
    return 'byteLength' in bytes
        ? bytes.byteLength === expectedLength
        : bytes.length === expectedLength;
}


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/binary.js":
/*!*************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/binary.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

/* eslint-disable func-style */
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.transactionID = exports.sha512Half = exports.binaryToJSON = exports.signingClaimData = exports.signingData = exports.multiSigningData = exports.readJSON = exports.serializeObject = exports.makeParser = exports.BytesList = exports.BinarySerializer = exports.BinaryParser = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const types_1 = __webpack_require__(/*! ./types */ "../../node_modules/ripple-binary-codec/dist/types/index.js");
const binary_parser_1 = __webpack_require__(/*! ./serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
Object.defineProperty(exports, "BinaryParser", ({ enumerable: true, get: function () { return binary_parser_1.BinaryParser; } }));
const hash_prefixes_1 = __webpack_require__(/*! ./hash-prefixes */ "../../node_modules/ripple-binary-codec/dist/hash-prefixes.js");
const binary_serializer_1 = __webpack_require__(/*! ./serdes/binary-serializer */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js");
Object.defineProperty(exports, "BinarySerializer", ({ enumerable: true, get: function () { return binary_serializer_1.BinarySerializer; } }));
Object.defineProperty(exports, "BytesList", ({ enumerable: true, get: function () { return binary_serializer_1.BytesList; } }));
const hashes_1 = __webpack_require__(/*! ./hashes */ "../../node_modules/ripple-binary-codec/dist/hashes.js");
Object.defineProperty(exports, "sha512Half", ({ enumerable: true, get: function () { return hashes_1.sha512Half; } }));
Object.defineProperty(exports, "transactionID", ({ enumerable: true, get: function () { return hashes_1.transactionID; } }));
const enums_1 = __webpack_require__(/*! ./enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
/**
 * Construct a BinaryParser
 *
 * @param bytes hex-string or Uint8Array to construct BinaryParser from
 * @param definitions rippled definitions used to parse the values of transaction types and such.
 *                          Can be customized for sidechains and amendments.
 * @returns BinaryParser
 */
const makeParser = (bytes, definitions) => new binary_parser_1.BinaryParser(bytes instanceof Uint8Array ? (0, utils_1.bytesToHex)(bytes) : bytes, definitions);
exports.makeParser = makeParser;
/**
 * Parse BinaryParser into JSON
 *
 * @param parser BinaryParser object
 * @param definitions rippled definitions used to parse the values of transaction types and such.
 *                          Can be customized for sidechains and amendments.
 * @returns JSON for the bytes in the BinaryParser
 */
const readJSON = (parser, definitions = enums_1.DEFAULT_DEFINITIONS) => parser.readType(types_1.coreTypes.STObject).toJSON(definitions);
exports.readJSON = readJSON;
/**
 * Parse a hex-string into its JSON interpretation
 *
 * @param bytes hex-string to parse into JSON
 * @param definitions rippled definitions used to parse the values of transaction types and such.
 *                          Can be customized for sidechains and amendments.
 * @returns JSON
 */
const binaryToJSON = (bytes, definitions) => readJSON(makeParser(bytes, definitions), definitions);
exports.binaryToJSON = binaryToJSON;
/**
 * Function to serialize JSON object representing a transaction
 *
 * @param object JSON object to serialize
 * @param opts options for serializing, including optional prefix, suffix, signingFieldOnly, and definitions
 * @returns A Uint8Array containing the serialized object
 */
function serializeObject(object, opts = {}) {
    const { prefix, suffix, signingFieldsOnly = false, definitions } = opts;
    const bytesList = new binary_serializer_1.BytesList();
    if (prefix) {
        bytesList.put(prefix);
    }
    const filter = signingFieldsOnly
        ? (f) => f.isSigningField
        : undefined;
    types_1.coreTypes.STObject
        .from(object, filter, definitions)
        .toBytesSink(bytesList);
    if (suffix) {
        bytesList.put(suffix);
    }
    return bytesList.toBytes();
}
exports.serializeObject = serializeObject;
/**
 * Serialize an object for signing
 *
 * @param transaction Transaction to serialize
 * @param prefix Prefix bytes to put before the serialized object
 * @param opts.definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns A Uint8Array with the serialized object
 */
function signingData(transaction, prefix = hash_prefixes_1.HashPrefix.transactionSig, opts = {}) {
    return serializeObject(transaction, {
        prefix,
        signingFieldsOnly: true,
        definitions: opts.definitions,
    });
}
exports.signingData = signingData;
/**
 * Serialize a signingClaim
 *
 * @param claim A claim object to serialize
 * @param opts.definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns the serialized object with appropriate prefix
 */
function signingClaimData(claim) {
    const num = BigInt(String(claim.amount));
    const prefix = hash_prefixes_1.HashPrefix.paymentChannelClaim;
    const channel = types_1.coreTypes.Hash256.from(claim.channel).toBytes();
    const amount = types_1.coreTypes.UInt64.from(num).toBytes();
    const bytesList = new binary_serializer_1.BytesList();
    bytesList.put(prefix);
    bytesList.put(channel);
    bytesList.put(amount);
    return bytesList.toBytes();
}
exports.signingClaimData = signingClaimData;
/**
 * Serialize a transaction object for multiSigning
 *
 * @param transaction transaction to serialize
 * @param signingAccount Account to sign the transaction with
 * @param opts.definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns serialized transaction with appropriate prefix and suffix
 */
function multiSigningData(transaction, signingAccount, opts = {
    definitions: enums_1.DEFAULT_DEFINITIONS,
}) {
    const prefix = hash_prefixes_1.HashPrefix.transactionMultiSig;
    const suffix = types_1.coreTypes.AccountID.from(signingAccount).toBytes();
    return serializeObject(transaction, {
        prefix,
        suffix,
        signingFieldsOnly: true,
        definitions: opts.definitions,
    });
}
exports.multiSigningData = multiSigningData;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/coretypes.js":
/*!****************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/coretypes.js ***!
  \****************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.types = exports.ShaMap = exports.HashPrefix = exports.quality = exports.TransactionResult = exports.Type = exports.LedgerEntryType = exports.TransactionType = exports.Field = exports.DEFAULT_DEFINITIONS = exports.ledgerHashes = exports.binary = exports.hashes = void 0;
const enums_1 = __webpack_require__(/*! ./enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
Object.defineProperty(exports, "DEFAULT_DEFINITIONS", ({ enumerable: true, get: function () { return enums_1.DEFAULT_DEFINITIONS; } }));
Object.defineProperty(exports, "Field", ({ enumerable: true, get: function () { return enums_1.Field; } }));
Object.defineProperty(exports, "TransactionType", ({ enumerable: true, get: function () { return enums_1.TransactionType; } }));
Object.defineProperty(exports, "LedgerEntryType", ({ enumerable: true, get: function () { return enums_1.LedgerEntryType; } }));
Object.defineProperty(exports, "Type", ({ enumerable: true, get: function () { return enums_1.Type; } }));
Object.defineProperty(exports, "TransactionResult", ({ enumerable: true, get: function () { return enums_1.TransactionResult; } }));
const types = __importStar(__webpack_require__(/*! ./types */ "../../node_modules/ripple-binary-codec/dist/types/index.js"));
exports.types = types;
const binary = __importStar(__webpack_require__(/*! ./binary */ "../../node_modules/ripple-binary-codec/dist/binary.js"));
exports.binary = binary;
const shamap_1 = __webpack_require__(/*! ./shamap */ "../../node_modules/ripple-binary-codec/dist/shamap.js");
Object.defineProperty(exports, "ShaMap", ({ enumerable: true, get: function () { return shamap_1.ShaMap; } }));
const ledgerHashes = __importStar(__webpack_require__(/*! ./ledger-hashes */ "../../node_modules/ripple-binary-codec/dist/ledger-hashes.js"));
exports.ledgerHashes = ledgerHashes;
const hashes = __importStar(__webpack_require__(/*! ./hashes */ "../../node_modules/ripple-binary-codec/dist/hashes.js"));
exports.hashes = hashes;
const quality_1 = __webpack_require__(/*! ./quality */ "../../node_modules/ripple-binary-codec/dist/quality.js");
Object.defineProperty(exports, "quality", ({ enumerable: true, get: function () { return quality_1.quality; } }));
const hash_prefixes_1 = __webpack_require__(/*! ./hash-prefixes */ "../../node_modules/ripple-binary-codec/dist/hash-prefixes.js");
Object.defineProperty(exports, "HashPrefix", ({ enumerable: true, get: function () { return hash_prefixes_1.HashPrefix; } }));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/bytes.js":
/*!******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/bytes.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.BytesLookup = exports.Bytes = void 0;
/*
 * @brief: Bytes, name, and ordinal representing one type, ledger_type, transaction type, or result
 */
class Bytes {
    constructor(name, ordinal, ordinalWidth) {
        this.name = name;
        this.ordinal = ordinal;
        this.ordinalWidth = ordinalWidth;
        this.bytes = new Uint8Array(ordinalWidth);
        for (let i = 0; i < ordinalWidth; i++) {
            this.bytes[ordinalWidth - i - 1] = (ordinal >>> (i * 8)) & 0xff;
        }
    }
    toJSON() {
        return this.name;
    }
    toBytesSink(sink) {
        sink.put(this.bytes);
    }
    toBytes() {
        return this.bytes;
    }
}
exports.Bytes = Bytes;
/*
 * @brief: Collection of Bytes objects, mapping bidirectionally
 */
class BytesLookup {
    constructor(types, ordinalWidth) {
        this.ordinalWidth = ordinalWidth;
        Object.entries(types).forEach(([k, v]) => {
            this.add(k, v);
        });
    }
    /**
     * Add a new name value pair to the BytesLookup.
     *
     * @param name - A human readable name for the field.
     * @param value - The numeric value for the field.
     * @throws if the name or value already exist in the lookup because it's unclear how to decode.
     */
    add(name, value) {
        if (this[name]) {
            throw new SyntaxError(`Attempted to add a value with a duplicate name "${name}". This is not allowed because it is unclear how to decode.`);
        }
        if (this[value.toString()]) {
            throw new SyntaxError(`Attempted to add a duplicate value under a different name (Given name: "${name}" and previous name: "${this[value.toString()]}. This is not allowed because it is unclear how to decode.\nGiven value: ${value.toString()}`);
        }
        this[name] = new Bytes(name, value, this.ordinalWidth);
        this[value.toString()] = this[name];
    }
    from(value) {
        return value instanceof Bytes ? value : this[value];
    }
    fromParser(parser) {
        return this.from(parser.readUIntN(this.ordinalWidth).toString());
    }
}
exports.BytesLookup = BytesLookup;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/constants.js":
/*!**********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/constants.js ***!
  \**********************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.TRANSACTION_RESULT_WIDTH = exports.TRANSACTION_TYPE_WIDTH = exports.LEDGER_ENTRY_WIDTH = exports.TYPE_WIDTH = void 0;
exports.TYPE_WIDTH = 2;
exports.LEDGER_ENTRY_WIDTH = 2;
exports.TRANSACTION_TYPE_WIDTH = 2;
exports.TRANSACTION_RESULT_WIDTH = 1;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/field.js":
/*!******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/field.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.FieldLookup = void 0;
const bytes_1 = __webpack_require__(/*! ./bytes */ "../../node_modules/ripple-binary-codec/dist/enums/bytes.js");
const serialized_type_1 = __webpack_require__(/*! ../types/serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const constants_1 = __webpack_require__(/*! ./constants */ "../../node_modules/ripple-binary-codec/dist/enums/constants.js");
/*
 * @brief: Serialize a field based on type_code and Field.nth
 */
function fieldHeader(type, nth) {
    const header = [];
    if (type < 16) {
        if (nth < 16) {
            header.push((type << 4) | nth);
        }
        else {
            header.push(type << 4, nth);
        }
    }
    else if (nth < 16) {
        header.push(nth, type);
    }
    else {
        header.push(0, type, nth);
    }
    return Uint8Array.from(header);
}
function buildField([name, info], typeOrdinal) {
    const field = fieldHeader(typeOrdinal, info.nth);
    return {
        name: name,
        nth: info.nth,
        isVariableLengthEncoded: info.isVLEncoded,
        isSerialized: info.isSerialized,
        isSigningField: info.isSigningField,
        ordinal: (typeOrdinal << 16) | info.nth,
        type: new bytes_1.Bytes(info.type, typeOrdinal, constants_1.TYPE_WIDTH),
        header: field,
        associatedType: serialized_type_1.SerializedType, // For later assignment in ./types/index.js or Definitions.updateAll(...)
    };
}
/*
 * @brief: The collection of all fields as defined in definitions.json
 */
class FieldLookup {
    constructor(fields, types) {
        fields.forEach(([name, field_info]) => {
            const typeOrdinal = types[field_info.type];
            this[name] = buildField([name, field_info], typeOrdinal);
            this[this[name].ordinal.toString()] = this[name];
        });
    }
    fromString(value) {
        return this[value];
    }
}
exports.FieldLookup = FieldLookup;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/index.js":
/*!******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/index.js ***!
  \******************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.TRANSACTION_TYPES = exports.TransactionType = exports.TransactionResult = exports.LedgerEntryType = exports.Type = exports.Field = exports.DEFAULT_DEFINITIONS = exports.XrplDefinitionsBase = exports.Bytes = void 0;
const definitions_json_1 = __importDefault(__webpack_require__(/*! ./definitions.json */ "../../node_modules/ripple-binary-codec/dist/enums/definitions.json"));
const xrpl_definitions_base_1 = __webpack_require__(/*! ./xrpl-definitions-base */ "../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions-base.js");
Object.defineProperty(exports, "XrplDefinitionsBase", ({ enumerable: true, get: function () { return xrpl_definitions_base_1.XrplDefinitionsBase; } }));
Object.defineProperty(exports, "Bytes", ({ enumerable: true, get: function () { return xrpl_definitions_base_1.Bytes; } }));
/**
 * By default, coreTypes from the `types` folder is where known type definitions are initialized to avoid import cycles.
 */
const DEFAULT_DEFINITIONS = new xrpl_definitions_base_1.XrplDefinitionsBase(definitions_json_1.default, {});
exports.DEFAULT_DEFINITIONS = DEFAULT_DEFINITIONS;
const Type = DEFAULT_DEFINITIONS.type;
exports.Type = Type;
const LedgerEntryType = DEFAULT_DEFINITIONS.ledgerEntryType;
exports.LedgerEntryType = LedgerEntryType;
const TransactionType = DEFAULT_DEFINITIONS.transactionType;
exports.TransactionType = TransactionType;
const TransactionResult = DEFAULT_DEFINITIONS.transactionResult;
exports.TransactionResult = TransactionResult;
const Field = DEFAULT_DEFINITIONS.field;
exports.Field = Field;
/*
 * @brief: All valid transaction types
 */
const TRANSACTION_TYPES = DEFAULT_DEFINITIONS.transactionNames;
exports.TRANSACTION_TYPES = TRANSACTION_TYPES;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions-base.js":
/*!**********************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions-base.js ***!
  \**********************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.BytesLookup = exports.Bytes = exports.FieldLookup = exports.XrplDefinitionsBase = void 0;
const bytes_1 = __webpack_require__(/*! ./bytes */ "../../node_modules/ripple-binary-codec/dist/enums/bytes.js");
Object.defineProperty(exports, "Bytes", ({ enumerable: true, get: function () { return bytes_1.Bytes; } }));
Object.defineProperty(exports, "BytesLookup", ({ enumerable: true, get: function () { return bytes_1.BytesLookup; } }));
const field_1 = __webpack_require__(/*! ./field */ "../../node_modules/ripple-binary-codec/dist/enums/field.js");
Object.defineProperty(exports, "FieldLookup", ({ enumerable: true, get: function () { return field_1.FieldLookup; } }));
const constants_1 = __webpack_require__(/*! ./constants */ "../../node_modules/ripple-binary-codec/dist/enums/constants.js");
/**
 * Stores the various types and fields for rippled to be used to encode/decode information later on.
 * XrplDefinitions should be instantiated instead of this class.
 */
class XrplDefinitionsBase {
    /**
     * Present rippled types in a typed and updatable format.
     * For an example of the input format see `definitions.json`.
     * To generate a new definitions file from rippled source code, use the tool at
     * `packages/ripple-binary-codec/tools/generateDefinitions.js`.
     *
     * See the definitions.test.js file for examples of how to create your own updated definitions.json.
     *
     * @param enums - A json encoding of the core types, transaction types, transaction results, transaction names, and fields.
     * @param types - A list of type objects with the same name as the fields defined.
     *              You can use the coreTypes object if you are not adding new types.
     */
    constructor(enums, types) {
        this.type = new bytes_1.BytesLookup(enums.TYPES, constants_1.TYPE_WIDTH);
        this.ledgerEntryType = new bytes_1.BytesLookup(enums.LEDGER_ENTRY_TYPES, constants_1.LEDGER_ENTRY_WIDTH);
        this.transactionType = new bytes_1.BytesLookup(enums.TRANSACTION_TYPES, constants_1.TRANSACTION_TYPE_WIDTH);
        this.transactionResult = new bytes_1.BytesLookup(enums.TRANSACTION_RESULTS, constants_1.TRANSACTION_RESULT_WIDTH);
        this.field = new field_1.FieldLookup(enums.FIELDS, enums.TYPES);
        this.transactionNames = Object.entries(enums.TRANSACTION_TYPES)
            .filter(([_key, value]) => value >= 0)
            .map(([key, _value]) => key);
        this.dataTypes = {}; // Filled in via associateTypes
        this.associateTypes(types);
    }
    /**
     * Associates each Field to a corresponding class that TypeScript can recognize.
     *
     * @param types a list of type objects with the same name as the fields defined.
     *              Defaults to xrpl.js's core type definitions.
     */
    associateTypes(types) {
        // Overwrite any existing type definitions with the given types
        this.dataTypes = Object.assign({}, this.dataTypes, types);
        Object.values(this.field).forEach((field) => {
            field.associatedType = this.dataTypes[field.type.name];
        });
        this.field['TransactionType'].associatedType = this.transactionType;
        this.field['TransactionResult'].associatedType = this.transactionResult;
        this.field['LedgerEntryType'].associatedType = this.ledgerEntryType;
    }
    getAssociatedTypes() {
        return this.dataTypes;
    }
}
exports.XrplDefinitionsBase = XrplDefinitionsBase;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions.js":
/*!*****************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions.js ***!
  \*****************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.XrplDefinitions = void 0;
const xrpl_definitions_base_1 = __webpack_require__(/*! ./xrpl-definitions-base */ "../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions-base.js");
const types_1 = __webpack_require__(/*! ../types */ "../../node_modules/ripple-binary-codec/dist/types/index.js");
/**
 * Stores the various types and fields for rippled to be used to encode/decode information later on.
 * Should be used instead of XrplDefinitionsBase since this defines default `types` for serializing/deserializing
 * ledger data.
 */
class XrplDefinitions extends xrpl_definitions_base_1.XrplDefinitionsBase {
    /**
     * Present rippled types in a typed and updatable format.
     * For an example of the input format see `definitions.json`
     * To generate a new definitions file from rippled source code, use the tool at
     * `packages/ripple-binary-codec/tools/generateDefinitions.js`.
     *
     * See the definitions.test.js file for examples of how to create your own updated definitions.json.
     *
     * @param enums - A json encoding of the core types, transaction types, transaction results, transaction names, and fields.
     * @param additionalTypes - A list of SerializedType objects with the same name as the fields defined.
     *              These types will be included in addition to the coreTypes used on mainnet.
     */
    constructor(enums, additionalTypes) {
        const types = Object.assign({}, types_1.coreTypes, additionalTypes);
        super(enums, types);
    }
}
exports.XrplDefinitions = XrplDefinitions;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/hash-prefixes.js":
/*!********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/hash-prefixes.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.HashPrefix = void 0;
const utils_1 = __webpack_require__(/*! ./utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
/**
 * Write a 32 bit integer to a Uint8Array
 *
 * @param uint32 32 bit integer to write to Uint8Array
 * @returns a Uint8Array with the bytes representation of uint32
 */
function bytes(uint32) {
    const result = new Uint8Array(4);
    (0, utils_1.writeUInt32BE)(result, uint32, 0);
    return result;
}
/**
 * Maps HashPrefix names to their byte representation
 */
const HashPrefix = {
    transactionID: bytes(0x54584e00),
    // transaction plus metadata
    transaction: bytes(0x534e4400),
    // account state
    accountStateEntry: bytes(0x4d4c4e00),
    // inner node in tree
    innerNode: bytes(0x4d494e00),
    // ledger master data for signing
    ledgerHeader: bytes(0x4c575200),
    // inner transaction to sign
    transactionSig: bytes(0x53545800),
    // inner transaction to sign
    transactionMultiSig: bytes(0x534d5400),
    // validation for signing
    validation: bytes(0x56414c00),
    // proposal for signing
    proposal: bytes(0x50525000),
    // payment channel claim
    paymentChannelClaim: bytes(0x434c4d00),
};
exports.HashPrefix = HashPrefix;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/hashes.js":
/*!*************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/hashes.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.transactionID = exports.sha512Half = exports.Sha512Half = void 0;
const hash_prefixes_1 = __webpack_require__(/*! ./hash-prefixes */ "../../node_modules/ripple-binary-codec/dist/hash-prefixes.js");
const types_1 = __webpack_require__(/*! ./types */ "../../node_modules/ripple-binary-codec/dist/types/index.js");
const binary_serializer_1 = __webpack_require__(/*! ./serdes/binary-serializer */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js");
const sha512_1 = __webpack_require__(/*! @xrplf/isomorphic/sha512 */ "../../node_modules/@xrplf/isomorphic/dist/sha512/browser.js");
/**
 * Class for hashing with SHA512
 * @extends BytesList So SerializedTypes can write bytes to a Sha512Half
 */
class Sha512Half extends binary_serializer_1.BytesList {
    constructor() {
        super(...arguments);
        this.hash = sha512_1.sha512.create();
    }
    /**
     * Construct a new Sha512Hash and write bytes this.hash
     *
     * @param bytes bytes to write to this.hash
     * @returns the new Sha512Hash object
     */
    static put(bytes) {
        return new Sha512Half().put(bytes);
    }
    /**
     * Write bytes to an existing Sha512Hash
     *
     * @param bytes bytes to write to object
     * @returns the Sha512 object
     */
    put(bytes) {
        this.hash.update(bytes);
        return this;
    }
    /**
     * Compute SHA512 hash and slice in half
     *
     * @returns half of a SHA512 hash
     */
    finish256() {
        return Uint8Array.from(this.hash.digest().slice(0, 32));
    }
    /**
     * Constructs a Hash256 from the Sha512Half object
     *
     * @returns a Hash256 object
     */
    finish() {
        return new types_1.Hash256(this.finish256());
    }
}
exports.Sha512Half = Sha512Half;
/**
 * compute SHA512 hash of a list of bytes
 *
 * @param args zero or more arguments to hash
 * @returns the sha512half hash of the arguments.
 */
function sha512Half(...args) {
    const hash = new Sha512Half();
    args.forEach((a) => hash.put(a));
    return hash.finish256();
}
exports.sha512Half = sha512Half;
/**
 * Construct a transactionID from a Serialized Transaction
 *
 * @param serialized bytes to hash
 * @returns a Hash256 object
 */
function transactionID(serialized) {
    return new types_1.Hash256(sha512Half(hash_prefixes_1.HashPrefix.transactionID, serialized));
}
exports.transactionID = transactionID;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/index.js":
/*!************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/index.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.coreTypes = exports.DEFAULT_DEFINITIONS = exports.XrplDefinitionsBase = exports.XrplDefinitions = exports.TRANSACTION_TYPES = exports.decodeLedgerData = exports.decodeQuality = exports.encodeQuality = exports.encodeForMultisigning = exports.encodeForSigningClaim = exports.encodeForSigning = exports.encode = exports.decode = void 0;
const coretypes_1 = __webpack_require__(/*! ./coretypes */ "../../node_modules/ripple-binary-codec/dist/coretypes.js");
const ledger_hashes_1 = __webpack_require__(/*! ./ledger-hashes */ "../../node_modules/ripple-binary-codec/dist/ledger-hashes.js");
Object.defineProperty(exports, "decodeLedgerData", ({ enumerable: true, get: function () { return ledger_hashes_1.decodeLedgerData; } }));
const enums_1 = __webpack_require__(/*! ./enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
Object.defineProperty(exports, "XrplDefinitionsBase", ({ enumerable: true, get: function () { return enums_1.XrplDefinitionsBase; } }));
Object.defineProperty(exports, "TRANSACTION_TYPES", ({ enumerable: true, get: function () { return enums_1.TRANSACTION_TYPES; } }));
Object.defineProperty(exports, "DEFAULT_DEFINITIONS", ({ enumerable: true, get: function () { return enums_1.DEFAULT_DEFINITIONS; } }));
const xrpl_definitions_1 = __webpack_require__(/*! ./enums/xrpl-definitions */ "../../node_modules/ripple-binary-codec/dist/enums/xrpl-definitions.js");
Object.defineProperty(exports, "XrplDefinitions", ({ enumerable: true, get: function () { return xrpl_definitions_1.XrplDefinitions; } }));
const types_1 = __webpack_require__(/*! ./types */ "../../node_modules/ripple-binary-codec/dist/types/index.js");
Object.defineProperty(exports, "coreTypes", ({ enumerable: true, get: function () { return types_1.coreTypes; } }));
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const { signingData, signingClaimData, multiSigningData, binaryToJSON, serializeObject, } = coretypes_1.binary;
/**
 * Decode a transaction
 *
 * @param binary hex-string of the encoded transaction
 * @param definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns the JSON representation of the transaction
 */
function decode(binary, definitions) {
    if (typeof binary !== 'string') {
        throw new Error('binary must be a hex string');
    }
    return binaryToJSON(binary, definitions);
}
exports.decode = decode;
/**
 * Encode a transaction
 *
 * @param json The JSON representation of a transaction
 * @param definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 *
 * @returns A hex-string of the encoded transaction
 */
function encode(json, definitions) {
    if (typeof json !== 'object') {
        throw new Error();
    }
    return (0, utils_1.bytesToHex)(serializeObject(json, { definitions }));
}
exports.encode = encode;
/**
 * Encode a transaction and prepare for signing
 *
 * @param json JSON object representing the transaction
 * @param signer string representing the account to sign the transaction with
 * @param definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns a hex string of the encoded transaction
 */
function encodeForSigning(json, definitions) {
    if (typeof json !== 'object') {
        throw new Error();
    }
    return (0, utils_1.bytesToHex)(signingData(json, coretypes_1.HashPrefix.transactionSig, {
        definitions,
    }));
}
exports.encodeForSigning = encodeForSigning;
/**
 * Encode a transaction and prepare for signing with a claim
 *
 * @param json JSON object representing the transaction
 * @param signer string representing the account to sign the transaction with
 * @param definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns a hex string of the encoded transaction
 */
function encodeForSigningClaim(json) {
    if (typeof json !== 'object') {
        throw new Error();
    }
    return (0, utils_1.bytesToHex)(signingClaimData(json));
}
exports.encodeForSigningClaim = encodeForSigningClaim;
/**
 * Encode a transaction and prepare for multi-signing
 *
 * @param json JSON object representing the transaction
 * @param signer string representing the account to sign the transaction with
 * @param definitions Custom rippled types to use instead of the default. Used for sidechains and amendments.
 * @returns a hex string of the encoded transaction
 */
function encodeForMultisigning(json, signer, definitions) {
    if (typeof json !== 'object') {
        throw new Error();
    }
    if (json['SigningPubKey'] !== '') {
        throw new Error();
    }
    const definitionsOpt = definitions ? { definitions } : undefined;
    return (0, utils_1.bytesToHex)(multiSigningData(json, signer, definitionsOpt));
}
exports.encodeForMultisigning = encodeForMultisigning;
/**
 * Encode a quality value
 *
 * @param value string representation of a number
 * @returns a hex-string representing the quality
 */
function encodeQuality(value) {
    if (typeof value !== 'string') {
        throw new Error();
    }
    return (0, utils_1.bytesToHex)(coretypes_1.quality.encode(value));
}
exports.encodeQuality = encodeQuality;
/**
 * Decode a quality value
 *
 * @param value hex-string of a quality
 * @returns a string representing the quality
 */
function decodeQuality(value) {
    if (typeof value !== 'string') {
        throw new Error();
    }
    return coretypes_1.quality.decode(value).toString();
}
exports.decodeQuality = decodeQuality;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/ledger-hashes.js":
/*!********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/ledger-hashes.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.decodeLedgerData = exports.ledgerHash = exports.transactionTreeHash = exports.accountStateHash = void 0;
const shamap_1 = __webpack_require__(/*! ./shamap */ "../../node_modules/ripple-binary-codec/dist/shamap.js");
const hash_prefixes_1 = __webpack_require__(/*! ./hash-prefixes */ "../../node_modules/ripple-binary-codec/dist/hash-prefixes.js");
const hashes_1 = __webpack_require__(/*! ./hashes */ "../../node_modules/ripple-binary-codec/dist/hashes.js");
const binary_1 = __webpack_require__(/*! ./binary */ "../../node_modules/ripple-binary-codec/dist/binary.js");
const hash_256_1 = __webpack_require__(/*! ./types/hash-256 */ "../../node_modules/ripple-binary-codec/dist/types/hash-256.js");
const st_object_1 = __webpack_require__(/*! ./types/st-object */ "../../node_modules/ripple-binary-codec/dist/types/st-object.js");
const uint_64_1 = __webpack_require__(/*! ./types/uint-64 */ "../../node_modules/ripple-binary-codec/dist/types/uint-64.js");
const uint_32_1 = __webpack_require__(/*! ./types/uint-32 */ "../../node_modules/ripple-binary-codec/dist/types/uint-32.js");
const uint_8_1 = __webpack_require__(/*! ./types/uint-8 */ "../../node_modules/ripple-binary-codec/dist/types/uint-8.js");
const binary_parser_1 = __webpack_require__(/*! ./serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
/**
 * Computes the hash of a list of objects
 *
 * @param itemizer Converts an item into a format that can be added to SHAMap
 * @param itemsJson Array of items to add to a SHAMap
 * @returns the hash of the SHAMap
 */
function computeHash(itemizer, itemsJson) {
    const map = new shamap_1.ShaMap();
    itemsJson.forEach((item) => map.addItem(...itemizer(item)));
    return map.hash();
}
/**
 * Convert a transaction into an index and an item
 *
 * @param json transaction with metadata
 * @returns a tuple of index and item to be added to SHAMap
 */
function transactionItemizer(json) {
    if (!json.hash) {
        throw new Error();
    }
    const index = hash_256_1.Hash256.from(json.hash);
    const item = {
        hashPrefix() {
            return hash_prefixes_1.HashPrefix.transaction;
        },
        toBytesSink(sink) {
            const serializer = new binary_1.BinarySerializer(sink);
            serializer.writeLengthEncoded(st_object_1.STObject.from(json));
            serializer.writeLengthEncoded(st_object_1.STObject.from(json.metaData));
        },
    };
    return [index, item, undefined];
}
/**
 * Convert an entry to a pair Hash256 and ShaMapNode
 *
 * @param json JSON describing a ledger entry item
 * @returns a tuple of index and item to be added to SHAMap
 */
function entryItemizer(json) {
    const index = hash_256_1.Hash256.from(json.index);
    const bytes = (0, binary_1.serializeObject)(json);
    const item = {
        hashPrefix() {
            return hash_prefixes_1.HashPrefix.accountStateEntry;
        },
        toBytesSink(sink) {
            sink.put(bytes);
        },
    };
    return [index, item, undefined];
}
/**
 * Function computing the hash of a transaction tree
 *
 * @param param An array of transaction objects to hash
 * @returns A Hash256 object
 */
function transactionTreeHash(param) {
    const itemizer = transactionItemizer;
    return computeHash(itemizer, param);
}
exports.transactionTreeHash = transactionTreeHash;
/**
 * Function computing the hash of accountState
 *
 * @param param A list of accountStates hash
 * @returns A Hash256 object
 */
function accountStateHash(param) {
    const itemizer = entryItemizer;
    return computeHash(itemizer, param);
}
exports.accountStateHash = accountStateHash;
/**
 * Serialize and hash a ledger header
 *
 * @param header a ledger header
 * @returns the hash of header
 */
function ledgerHash(header) {
    const hash = new hashes_1.Sha512Half();
    hash.put(hash_prefixes_1.HashPrefix.ledgerHeader);
    if (header.parent_close_time === undefined ||
        header.close_flags === undefined) {
        throw new Error();
    }
    uint_32_1.UInt32.from(header.ledger_index).toBytesSink(hash);
    uint_64_1.UInt64.from(BigInt(String(header.total_coins))).toBytesSink(hash);
    hash_256_1.Hash256.from(header.parent_hash).toBytesSink(hash);
    hash_256_1.Hash256.from(header.transaction_hash).toBytesSink(hash);
    hash_256_1.Hash256.from(header.account_hash).toBytesSink(hash);
    uint_32_1.UInt32.from(header.parent_close_time).toBytesSink(hash);
    uint_32_1.UInt32.from(header.close_time).toBytesSink(hash);
    uint_8_1.UInt8.from(header.close_time_resolution).toBytesSink(hash);
    uint_8_1.UInt8.from(header.close_flags).toBytesSink(hash);
    return hash.finish();
}
exports.ledgerHash = ledgerHash;
/**
 * Decodes a serialized ledger header
 *
 * @param binary A serialized ledger header
 * @param definitions Type definitions to parse the ledger objects.
 *      Used if there are non-default ledger objects to decode.
 * @returns A JSON object describing a ledger header
 */
function decodeLedgerData(binary, definitions) {
    if (typeof binary !== 'string') {
        throw new Error('binary must be a hex string');
    }
    const parser = new binary_parser_1.BinaryParser(binary, definitions);
    return {
        ledger_index: parser.readUInt32(),
        total_coins: parser.readType(uint_64_1.UInt64).valueOf().toString(),
        parent_hash: parser.readType(hash_256_1.Hash256).toHex(),
        transaction_hash: parser.readType(hash_256_1.Hash256).toHex(),
        account_hash: parser.readType(hash_256_1.Hash256).toHex(),
        parent_close_time: parser.readUInt32(),
        close_time: parser.readUInt32(),
        close_time_resolution: parser.readUInt8(),
        close_flags: parser.readUInt8(),
    };
}
exports.decodeLedgerData = decodeLedgerData;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/quality.js":
/*!**************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/quality.js ***!
  \**************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.quality = void 0;
const types_1 = __webpack_require__(/*! ./types */ "../../node_modules/ripple-binary-codec/dist/types/index.js");
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * class for encoding and decoding quality
 */
class quality {
    /**
     * Encode quality amount
     *
     * @param arg string representation of an amount
     * @returns Serialized quality
     */
    static encode(quality) {
        const decimal = (0, bignumber_js_1.default)(quality);
        const exponent = ((decimal === null || decimal === void 0 ? void 0 : decimal.e) || 0) - 15;
        const qualityString = decimal.times(`1e${-exponent}`).abs().toString();
        const bytes = types_1.coreTypes.UInt64.from(BigInt(qualityString)).toBytes();
        bytes[0] = exponent + 100;
        return bytes;
    }
    /**
     * Decode quality amount
     *
     * @param arg hex-string denoting serialized quality
     * @returns deserialized quality
     */
    static decode(quality) {
        const bytes = (0, utils_1.hexToBytes)(quality).slice(-8);
        const exponent = bytes[0] - 100;
        const mantissa = new bignumber_js_1.default(`0x${(0, utils_1.bytesToHex)(bytes.slice(1))}`);
        return mantissa.times(`1e${exponent}`);
    }
}
exports.quality = quality;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js":
/*!***************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js ***!
  \***************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.BinaryParser = void 0;
const enums_1 = __webpack_require__(/*! ../enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * BinaryParser is used to compute fields and values from a HexString
 */
class BinaryParser {
    /**
     * Initialize bytes to a hex string
     *
     * @param hexBytes a hex string
     * @param definitions Rippled definitions used to parse the values of transaction types and such.
     *                          Can be customized for sidechains and amendments.
     */
    constructor(hexBytes, definitions = enums_1.DEFAULT_DEFINITIONS) {
        this.bytes = (0, utils_1.hexToBytes)(hexBytes);
        this.definitions = definitions;
    }
    /**
     * Peek the first byte of the BinaryParser
     *
     * @returns The first byte of the BinaryParser
     */
    peek() {
        if (this.bytes.byteLength === 0) {
            throw new Error();
        }
        return this.bytes[0];
    }
    /**
     * Consume the first n bytes of the BinaryParser
     *
     * @param n the number of bytes to skip
     */
    skip(n) {
        if (n > this.bytes.byteLength) {
            throw new Error();
        }
        this.bytes = this.bytes.slice(n);
    }
    /**
     * read the first n bytes from the BinaryParser
     *
     * @param n The number of bytes to read
     * @return The bytes
     */
    read(n) {
        if (n > this.bytes.byteLength) {
            throw new Error();
        }
        const slice = this.bytes.slice(0, n);
        this.skip(n);
        return slice;
    }
    /**
     * Read an integer of given size
     *
     * @param n The number of bytes to read
     * @return The number represented by those bytes
     */
    readUIntN(n) {
        if (0 >= n || n > 4) {
            throw new Error('invalid n');
        }
        return this.read(n).reduce((a, b) => (a << 8) | b) >>> 0;
    }
    readUInt8() {
        return this.readUIntN(1);
    }
    readUInt16() {
        return this.readUIntN(2);
    }
    readUInt32() {
        return this.readUIntN(4);
    }
    size() {
        return this.bytes.byteLength;
    }
    end(customEnd) {
        const length = this.bytes.byteLength;
        return length === 0 || (customEnd !== undefined && length <= customEnd);
    }
    /**
     * Reads variable length encoded bytes
     *
     * @return The variable length bytes
     */
    readVariableLength() {
        return this.read(this.readVariableLengthLength());
    }
    /**
     * Reads the length of the variable length encoded bytes
     *
     * @return The length of the variable length encoded bytes
     */
    readVariableLengthLength() {
        const b1 = this.readUInt8();
        if (b1 <= 192) {
            return b1;
        }
        else if (b1 <= 240) {
            const b2 = this.readUInt8();
            return 193 + (b1 - 193) * 256 + b2;
        }
        else if (b1 <= 254) {
            const b2 = this.readUInt8();
            const b3 = this.readUInt8();
            return 12481 + (b1 - 241) * 65536 + b2 * 256 + b3;
        }
        throw new Error('Invalid variable length indicator');
    }
    /**
     * Reads the field ordinal from the BinaryParser
     *
     * @return Field ordinal
     */
    readFieldOrdinal() {
        let type = this.readUInt8();
        let nth = type & 15;
        type >>= 4;
        if (type === 0) {
            type = this.readUInt8();
            if (type === 0 || type < 16) {
                throw new Error(`Cannot read FieldOrdinal, type_code ${type} out of range`);
            }
        }
        if (nth === 0) {
            nth = this.readUInt8();
            if (nth === 0 || nth < 16) {
                throw new Error(`Cannot read FieldOrdinal, field_code ${nth} out of range`);
            }
        }
        return (type << 16) | nth;
    }
    /**
     * Read the field from the BinaryParser
     *
     * @return The field represented by the bytes at the head of the BinaryParser
     */
    readField() {
        return this.definitions.field.fromString(this.readFieldOrdinal().toString());
    }
    /**
     * Read a given type from the BinaryParser
     *
     * @param type The type that you want to read from the BinaryParser
     * @return The instance of that type read from the BinaryParser
     */
    readType(type) {
        return type.fromParser(this);
    }
    /**
     * Get the type associated with a given field
     *
     * @param field The field that you wan to get the type of
     * @return The type associated with the given field
     */
    typeForField(field) {
        return field.associatedType;
    }
    /**
     * Read value of the type specified by field from the BinaryParser
     *
     * @param field The field that you want to get the associated value for
     * @return The value associated with the given field
     */
    readFieldValue(field) {
        const type = this.typeForField(field);
        if (!type) {
            throw new Error(`unsupported: (${field.name}, ${field.type.name})`);
        }
        const sizeHint = field.isVariableLengthEncoded
            ? this.readVariableLengthLength()
            : undefined;
        const value = type.fromParser(this, sizeHint);
        if (value === undefined) {
            throw new Error(`fromParser for (${field.name}, ${field.type.name}) -> undefined `);
        }
        return value;
    }
    /**
     * Get the next field and value from the BinaryParser
     *
     * @return The field and value
     */
    readFieldAndValue() {
        const field = this.readField();
        return [field, this.readFieldValue(field)];
    }
}
exports.BinaryParser = BinaryParser;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js":
/*!*******************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js ***!
  \*******************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.BinarySerializer = exports.BytesList = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * Bytes list is a collection of Uint8Array objects
 */
class BytesList {
    constructor() {
        this.bytesArray = [];
    }
    /**
     * Get the total number of bytes in the BytesList
     *
     * @return the number of bytes
     */
    getLength() {
        return (0, utils_1.concat)(this.bytesArray).byteLength;
    }
    /**
     * Put bytes in the BytesList
     *
     * @param bytesArg A Uint8Array
     * @return this BytesList
     */
    put(bytesArg) {
        const bytes = Uint8Array.from(bytesArg); // Temporary, to catch instances of Uint8Array being passed in
        this.bytesArray.push(bytes);
        return this;
    }
    /**
     * Write this BytesList to the back of another bytes list
     *
     *  @param list The BytesList to write to
     */
    toBytesSink(list) {
        list.put(this.toBytes());
    }
    toBytes() {
        return (0, utils_1.concat)(this.bytesArray);
    }
    toHex() {
        return (0, utils_1.bytesToHex)(this.toBytes());
    }
}
exports.BytesList = BytesList;
/**
 * BinarySerializer is used to write fields and values to Uint8Arrays
 */
class BinarySerializer {
    constructor(sink) {
        this.sink = new BytesList();
        this.sink = sink;
    }
    /**
     * Write a value to this BinarySerializer
     *
     * @param value a SerializedType value
     */
    write(value) {
        value.toBytesSink(this.sink);
    }
    /**
     * Write bytes to this BinarySerializer
     *
     * @param bytes the bytes to write
     */
    put(bytes) {
        this.sink.put(bytes);
    }
    /**
     * Write a value of a given type to this BinarySerializer
     *
     * @param type the type to write
     * @param value a value of that type
     */
    writeType(type, value) {
        this.write(type.from(value));
    }
    /**
     * Write BytesList to this BinarySerializer
     *
     * @param bl BytesList to write to BinarySerializer
     */
    writeBytesList(bl) {
        bl.toBytesSink(this.sink);
    }
    /**
     * Calculate the header of Variable Length encoded bytes
     *
     * @param length the length of the bytes
     */
    encodeVariableLength(length) {
        const lenBytes = new Uint8Array(3);
        if (length <= 192) {
            lenBytes[0] = length;
            return lenBytes.slice(0, 1);
        }
        else if (length <= 12480) {
            length -= 193;
            lenBytes[0] = 193 + (length >>> 8);
            lenBytes[1] = length & 0xff;
            return lenBytes.slice(0, 2);
        }
        else if (length <= 918744) {
            length -= 12481;
            lenBytes[0] = 241 + (length >>> 16);
            lenBytes[1] = (length >> 8) & 0xff;
            lenBytes[2] = length & 0xff;
            return lenBytes.slice(0, 3);
        }
        throw new Error('Overflow error');
    }
    /**
     * Write field and value to BinarySerializer
     *
     * @param field field to write to BinarySerializer
     * @param value value to write to BinarySerializer
     */
    writeFieldAndValue(field, value, isUnlModifyWorkaround = false) {
        const associatedValue = field.associatedType.from(value);
        if (associatedValue.toBytesSink === undefined || field.name === undefined) {
            throw new Error();
        }
        this.sink.put(field.header);
        if (field.isVariableLengthEncoded) {
            this.writeLengthEncoded(associatedValue, isUnlModifyWorkaround);
        }
        else {
            associatedValue.toBytesSink(this.sink);
        }
    }
    /**
     * Write a variable length encoded value to the BinarySerializer
     *
     * @param value length encoded value to write to BytesList
     */
    writeLengthEncoded(value, isUnlModifyWorkaround = false) {
        const bytes = new BytesList();
        if (!isUnlModifyWorkaround) {
            // this part doesn't happen for the Account field in a UNLModify transaction
            value.toBytesSink(bytes);
        }
        this.put(this.encodeVariableLength(bytes.getLength()));
        this.writeBytesList(bytes);
    }
}
exports.BinarySerializer = BinarySerializer;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/shamap.js":
/*!*************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/shamap.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.ShaMapLeaf = exports.ShaMapNode = exports.ShaMap = void 0;
const types_1 = __webpack_require__(/*! ./types */ "../../node_modules/ripple-binary-codec/dist/types/index.js");
const hash_prefixes_1 = __webpack_require__(/*! ./hash-prefixes */ "../../node_modules/ripple-binary-codec/dist/hash-prefixes.js");
const hashes_1 = __webpack_require__(/*! ./hashes */ "../../node_modules/ripple-binary-codec/dist/hashes.js");
/**
 * Abstract class describing a SHAMapNode
 */
class ShaMapNode {
}
exports.ShaMapNode = ShaMapNode;
/**
 * Class describing a Leaf of SHAMap
 */
class ShaMapLeaf extends ShaMapNode {
    constructor(index, item) {
        super();
        this.index = index;
        this.item = item;
    }
    /**
     * @returns true as ShaMapLeaf is a leaf node
     */
    isLeaf() {
        return true;
    }
    /**
     * @returns false as ShaMapLeaf is not an inner node
     */
    isInner() {
        return false;
    }
    /**
     * Get the prefix of the this.item
     *
     * @returns The hash prefix, unless this.item is undefined, then it returns an empty Uint8Array
     */
    hashPrefix() {
        return this.item === undefined ? new Uint8Array(0) : this.item.hashPrefix();
    }
    /**
     * Hash the bytes representation of this
     *
     * @returns hash of this.item concatenated with this.index
     */
    hash() {
        const hash = hashes_1.Sha512Half.put(this.hashPrefix());
        this.toBytesSink(hash);
        return hash.finish();
    }
    /**
     * Write the bytes representation of this to a BytesList
     * @param list BytesList to write bytes to
     */
    toBytesSink(list) {
        if (this.item !== undefined) {
            this.item.toBytesSink(list);
        }
        this.index.toBytesSink(list);
    }
}
exports.ShaMapLeaf = ShaMapLeaf;
/**
 * Class defining an Inner Node of a SHAMap
 */
class ShaMapInner extends ShaMapNode {
    constructor(depth = 0) {
        super();
        this.depth = depth;
        this.slotBits = 0;
        this.branches = Array(16);
    }
    /**
     * @returns true as ShaMapInner is an inner node
     */
    isInner() {
        return true;
    }
    /**
     * @returns false as ShaMapInner is not a leaf node
     */
    isLeaf() {
        return false;
    }
    /**
     * Get the hash prefix for this node
     *
     * @returns hash prefix describing an inner node
     */
    hashPrefix() {
        return hash_prefixes_1.HashPrefix.innerNode;
    }
    /**
     * Set a branch of this node to be another node
     *
     * @param slot Slot to add branch to this.branches
     * @param branch Branch to add
     */
    setBranch(slot, branch) {
        this.slotBits = this.slotBits | (1 << slot);
        this.branches[slot] = branch;
    }
    /**
     * @returns true if node is empty
     */
    empty() {
        return this.slotBits === 0;
    }
    /**
     * Compute the hash of this node
     *
     * @returns The hash of this node
     */
    hash() {
        if (this.empty()) {
            return types_1.coreTypes.Hash256.ZERO_256;
        }
        const hash = hashes_1.Sha512Half.put(this.hashPrefix());
        this.toBytesSink(hash);
        return hash.finish();
    }
    /**
     * Writes the bytes representation of this node to a BytesList
     *
     * @param list BytesList to write bytes to
     */
    toBytesSink(list) {
        for (let i = 0; i < this.branches.length; i++) {
            const branch = this.branches[i];
            const hash = branch
                ? branch.hash()
                : types_1.coreTypes.Hash256.ZERO_256;
            hash.toBytesSink(list);
        }
    }
    /**
     * Add item to the SHAMap
     *
     * @param index Hash of the index of the item being inserted
     * @param item Item to insert in the map
     * @param leaf Leaf node to insert when branch doesn't exist
     */
    addItem(index, item, leaf) {
        if (index === undefined) {
            throw new Error();
        }
        if (index !== undefined) {
            const nibble = index.nibblet(this.depth);
            const existing = this.branches[nibble];
            if (existing === undefined) {
                this.setBranch(nibble, leaf || new ShaMapLeaf(index, item));
            }
            else if (existing instanceof ShaMapLeaf) {
                const newInner = new ShaMapInner(this.depth + 1);
                newInner.addItem(existing.index, undefined, existing);
                newInner.addItem(index, item, leaf);
                this.setBranch(nibble, newInner);
            }
            else if (existing instanceof ShaMapInner) {
                existing.addItem(index, item, leaf);
            }
            else {
                throw new Error('invalid ShaMap.addItem call');
            }
        }
    }
}
class ShaMap extends ShaMapInner {
}
exports.ShaMap = ShaMap;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/account-id.js":
/*!***********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/account-id.js ***!
  \***********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.AccountID = void 0;
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const hash_160_1 = __webpack_require__(/*! ./hash-160 */ "../../node_modules/ripple-binary-codec/dist/types/hash-160.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const HEX_REGEX = /^[A-F0-9]{40}$/;
/**
 * Class defining how to encode and decode an AccountID
 */
class AccountID extends hash_160_1.Hash160 {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : AccountID.defaultAccountID.bytes);
    }
    /**
     * Defines how to construct an AccountID
     *
     * @param value either an existing AccountID, a hex-string, or a base58 r-Address
     * @returns an AccountID object
     */
    static from(value) {
        if (value instanceof AccountID) {
            return value;
        }
        if (typeof value === 'string') {
            if (value === '') {
                return new AccountID();
            }
            return HEX_REGEX.test(value)
                ? new AccountID((0, utils_1.hexToBytes)(value))
                : this.fromBase58(value);
        }
        throw new Error('Cannot construct AccountID from value given');
    }
    /**
     * Defines how to build an AccountID from a base58 r-Address
     *
     * @param value a base58 r-Address
     * @returns an AccountID object
     */
    static fromBase58(value) {
        if ((0, ripple_address_codec_1.isValidXAddress)(value)) {
            const classic = (0, ripple_address_codec_1.xAddressToClassicAddress)(value);
            if (classic.tag !== false)
                throw new Error('Only allowed to have tag on Account or Destination');
            value = classic.classicAddress;
        }
        return new AccountID(Uint8Array.from((0, ripple_address_codec_1.decodeAccountID)(value)));
    }
    /**
     * Overload of toJSON
     *
     * @returns the base58 string for this AccountID
     */
    toJSON() {
        return this.toBase58();
    }
    /**
     * Defines how to encode AccountID into a base58 address
     *
     * @returns the base58 string defined by this.bytes
     */
    toBase58() {
        return (0, ripple_address_codec_1.encodeAccountID)(this.bytes);
    }
}
exports.AccountID = AccountID;
AccountID.defaultAccountID = new AccountID(new Uint8Array(20));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/amount.js":
/*!*******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/amount.js ***!
  \*******************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Amount = void 0;
const binary_parser_1 = __webpack_require__(/*! ../serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
const account_id_1 = __webpack_require__(/*! ./account-id */ "../../node_modules/ripple-binary-codec/dist/types/account-id.js");
const currency_1 = __webpack_require__(/*! ./currency */ "../../node_modules/ripple-binary-codec/dist/types/currency.js");
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const utils_2 = __webpack_require__(/*! ../utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
const hash_192_1 = __webpack_require__(/*! ./hash-192 */ "../../node_modules/ripple-binary-codec/dist/types/hash-192.js");
/**
 * Constants for validating amounts
 */
const MIN_IOU_EXPONENT = -96;
const MAX_IOU_EXPONENT = 80;
const MAX_IOU_PRECISION = 16;
const MAX_DROPS = new bignumber_js_1.default('1e17');
const MIN_XRP = new bignumber_js_1.default('1e-6');
const mask = BigInt(0x00000000ffffffff);
const mptMask = BigInt(0x8000000000000000);
/**
 * BigNumber configuration for Amount IOUs
 */
bignumber_js_1.default.config({
    EXPONENTIAL_AT: [
        MIN_IOU_EXPONENT - MAX_IOU_PRECISION,
        MAX_IOU_EXPONENT + MAX_IOU_PRECISION,
    ],
});
/**
 * Type guard for AmountObjectIOU
 */
function isAmountObjectIOU(arg) {
    const keys = Object.keys(arg).sort();
    return (keys.length === 3 &&
        keys[0] === 'currency' &&
        keys[1] === 'issuer' &&
        keys[2] === 'value');
}
/**
 * Type guard for AmountObjectMPT
 */
function isAmountObjectMPT(arg) {
    const keys = Object.keys(arg).sort();
    return (keys.length === 2 && keys[0] === 'mpt_issuance_id' && keys[1] === 'value');
}
/**
 * Class for serializing/Deserializing Amounts
 */
class Amount extends serialized_type_1.SerializedType {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : Amount.defaultAmount.bytes);
    }
    /**
     * Construct an amount from an IOU, MPT or string amount
     *
     * @param value An Amount, object representing an IOU, or a string
     *     representing an integer amount
     * @returns An Amount object
     */
    static from(value) {
        if (value instanceof Amount) {
            return value;
        }
        let amount = new Uint8Array(8);
        if (typeof value === 'string') {
            Amount.assertXrpIsValid(value);
            const number = BigInt(value);
            const intBuf = [new Uint8Array(4), new Uint8Array(4)];
            (0, utils_2.writeUInt32BE)(intBuf[0], Number(number >> BigInt(32)), 0);
            (0, utils_2.writeUInt32BE)(intBuf[1], Number(number & BigInt(mask)), 0);
            amount = (0, utils_1.concat)(intBuf);
            amount[0] |= 0x40;
            return new Amount(amount);
        }
        if (isAmountObjectIOU(value)) {
            const number = new bignumber_js_1.default(value.value);
            Amount.assertIouIsValid(number);
            if (number.isZero()) {
                amount[0] |= 0x80;
            }
            else {
                const integerNumberString = number
                    .times(`1e${-((number.e || 0) - 15)}`)
                    .abs()
                    .toString();
                const num = BigInt(integerNumberString);
                const intBuf = [new Uint8Array(4), new Uint8Array(4)];
                (0, utils_2.writeUInt32BE)(intBuf[0], Number(num >> BigInt(32)), 0);
                (0, utils_2.writeUInt32BE)(intBuf[1], Number(num & BigInt(mask)), 0);
                amount = (0, utils_1.concat)(intBuf);
                amount[0] |= 0x80;
                if (number.gt(new bignumber_js_1.default(0))) {
                    amount[0] |= 0x40;
                }
                const exponent = (number.e || 0) - 15;
                const exponentByte = 97 + exponent;
                amount[0] |= exponentByte >>> 2;
                amount[1] |= (exponentByte & 0x03) << 6;
            }
            const currency = currency_1.Currency.from(value.currency).toBytes();
            const issuer = account_id_1.AccountID.from(value.issuer).toBytes();
            return new Amount((0, utils_1.concat)([amount, currency, issuer]));
        }
        if (isAmountObjectMPT(value)) {
            Amount.assertMptIsValid(value.value);
            let leadingByte = new Uint8Array(1);
            leadingByte[0] |= 0x60;
            const num = BigInt(value.value);
            const intBuf = [new Uint8Array(4), new Uint8Array(4)];
            (0, utils_2.writeUInt32BE)(intBuf[0], Number(num >> BigInt(32)), 0);
            (0, utils_2.writeUInt32BE)(intBuf[1], Number(num & BigInt(mask)), 0);
            amount = (0, utils_1.concat)(intBuf);
            const mptIssuanceID = hash_192_1.Hash192.from(value.mpt_issuance_id).toBytes();
            return new Amount((0, utils_1.concat)([leadingByte, amount, mptIssuanceID]));
        }
        throw new Error('Invalid type to construct an Amount');
    }
    /**
     * Read an amount from a BinaryParser
     *
     * @param parser BinaryParser to read the Amount from
     * @returns An Amount object
     */
    static fromParser(parser) {
        const isIOU = parser.peek() & 0x80;
        if (isIOU)
            return new Amount(parser.read(48));
        // the amount can be either MPT or XRP at this point
        const isMPT = parser.peek() & 0x20;
        const numBytes = isMPT ? 33 : 8;
        return new Amount(parser.read(numBytes));
    }
    /**
     * Get the JSON representation of this Amount
     *
     * @returns the JSON interpretation of this.bytes
     */
    toJSON() {
        if (this.isNative()) {
            const bytes = this.bytes;
            const isPositive = bytes[0] & 0x40;
            const sign = isPositive ? '' : '-';
            bytes[0] &= 0x3f;
            const msb = BigInt((0, utils_2.readUInt32BE)(bytes.slice(0, 4), 0));
            const lsb = BigInt((0, utils_2.readUInt32BE)(bytes.slice(4), 0));
            const num = (msb << BigInt(32)) | lsb;
            return `${sign}${num.toString()}`;
        }
        if (this.isIOU()) {
            const parser = new binary_parser_1.BinaryParser(this.toString());
            const mantissa = parser.read(8);
            const currency = currency_1.Currency.fromParser(parser);
            const issuer = account_id_1.AccountID.fromParser(parser);
            const b1 = mantissa[0];
            const b2 = mantissa[1];
            const isPositive = b1 & 0x40;
            const sign = isPositive ? '' : '-';
            const exponent = ((b1 & 0x3f) << 2) + ((b2 & 0xff) >> 6) - 97;
            mantissa[0] = 0;
            mantissa[1] &= 0x3f;
            const value = new bignumber_js_1.default(`${sign}0x${(0, utils_1.bytesToHex)(mantissa)}`).times(`1e${exponent}`);
            Amount.assertIouIsValid(value);
            return {
                value: value.toString(),
                currency: currency.toJSON(),
                issuer: issuer.toJSON(),
            };
        }
        if (this.isMPT()) {
            const parser = new binary_parser_1.BinaryParser(this.toString());
            const leadingByte = parser.read(1);
            const amount = parser.read(8);
            const mptID = hash_192_1.Hash192.fromParser(parser);
            const isPositive = leadingByte[0] & 0x40;
            const sign = isPositive ? '' : '-';
            const msb = BigInt((0, utils_2.readUInt32BE)(amount.slice(0, 4), 0));
            const lsb = BigInt((0, utils_2.readUInt32BE)(amount.slice(4), 0));
            const num = (msb << BigInt(32)) | lsb;
            return {
                value: `${sign}${num.toString()}`,
                mpt_issuance_id: mptID.toString(),
            };
        }
        throw new Error('Invalid amount to construct JSON');
    }
    /**
     * Validate XRP amount
     *
     * @param amount String representing XRP amount
     * @returns void, but will throw if invalid amount
     */
    static assertXrpIsValid(amount) {
        if (amount.indexOf('.') !== -1) {
            throw new Error(`${amount.toString()} is an illegal amount`);
        }
        const decimal = new bignumber_js_1.default(amount);
        if (!decimal.isZero()) {
            if (decimal.lt(MIN_XRP) || decimal.gt(MAX_DROPS)) {
                throw new Error(`${amount.toString()} is an illegal amount`);
            }
        }
    }
    /**
     * Validate IOU.value amount
     *
     * @param decimal BigNumber object representing IOU.value
     * @returns void, but will throw if invalid amount
     */
    static assertIouIsValid(decimal) {
        if (!decimal.isZero()) {
            const p = decimal.precision();
            const e = (decimal.e || 0) - 15;
            if (p > MAX_IOU_PRECISION ||
                e > MAX_IOU_EXPONENT ||
                e < MIN_IOU_EXPONENT) {
                throw new Error('Decimal precision out of range');
            }
            this.verifyNoDecimal(decimal);
        }
    }
    /**
     * Validate MPT.value amount
     *
     * @param decimal BigNumber object representing MPT.value
     * @returns void, but will throw if invalid amount
     */
    static assertMptIsValid(amount) {
        if (amount.indexOf('.') !== -1) {
            throw new Error(`${amount.toString()} is an illegal amount`);
        }
        const decimal = new bignumber_js_1.default(amount);
        if (!decimal.isZero()) {
            if (decimal < (0, bignumber_js_1.default)(0)) {
                throw new Error(`${amount.toString()} is an illegal amount`);
            }
            if (Number(BigInt(amount) & BigInt(mptMask)) != 0) {
                throw new Error(`${amount.toString()} is an illegal amount`);
            }
        }
    }
    /**
     * Ensure that the value after being multiplied by the exponent does not
     * contain a decimal.
     *
     * @param decimal a Decimal object
     * @returns a string of the object without a decimal
     */
    static verifyNoDecimal(decimal) {
        const integerNumberString = decimal
            .times(`1e${-((decimal.e || 0) - 15)}`)
            .abs()
            .toString();
        if (integerNumberString.indexOf('.') !== -1) {
            throw new Error('Decimal place found in integerNumberString');
        }
    }
    /**
     * Test if this amount is in units of Native Currency(XRP)
     *
     * @returns true if Native (XRP)
     */
    isNative() {
        return (this.bytes[0] & 0x80) === 0 && (this.bytes[0] & 0x20) === 0;
    }
    /**
     * Test if this amount is in units of MPT
     *
     * @returns true if MPT
     */
    isMPT() {
        return (this.bytes[0] & 0x80) === 0 && (this.bytes[0] & 0x20) !== 0;
    }
    /**
     * Test if this amount is in units of IOU
     *
     * @returns true if IOU
     */
    isIOU() {
        return (this.bytes[0] & 0x80) !== 0;
    }
}
exports.Amount = Amount;
Amount.defaultAmount = new Amount((0, utils_1.hexToBytes)('4000000000000000'));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/blob.js":
/*!*****************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/blob.js ***!
  \*****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Blob = void 0;
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * Variable length encoded type
 */
class Blob extends serialized_type_1.SerializedType {
    constructor(bytes) {
        super(bytes);
    }
    /**
     * Defines how to read a Blob from a BinaryParser
     *
     * @param parser The binary parser to read the Blob from
     * @param hint The length of the blob, computed by readVariableLengthLength() and passed in
     * @returns A Blob object
     */
    static fromParser(parser, hint) {
        return new Blob(parser.read(hint));
    }
    /**
     * Create a Blob object from a hex-string
     *
     * @param value existing Blob object or a hex-string
     * @returns A Blob object
     */
    static from(value) {
        if (value instanceof Blob) {
            return value;
        }
        if (typeof value === 'string') {
            if (!/^[A-F0-9]*$/iu.test(value)) {
                throw new Error('Cannot construct Blob from a non-hex string');
            }
            return new Blob((0, utils_1.hexToBytes)(value));
        }
        throw new Error('Cannot construct Blob from value given');
    }
}
exports.Blob = Blob;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/currency.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/currency.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Currency = void 0;
const hash_160_1 = __webpack_require__(/*! ./hash-160 */ "../../node_modules/ripple-binary-codec/dist/types/hash-160.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const XRP_HEX_REGEX = /^0{40}$/;
const ISO_REGEX = /^[A-Z0-9a-z?!@#$%^&*(){}[\]|]{3}$/;
const HEX_REGEX = /^[A-F0-9]{40}$/;
// eslint-disable-next-line no-control-regex
const STANDARD_FORMAT_HEX_REGEX = /^0{24}[\x00-\x7F]{6}0{10}$/;
/**
 * Convert an ISO code to a currency bytes representation
 */
function isoToBytes(iso) {
    const bytes = new Uint8Array(20);
    if (iso !== 'XRP') {
        const isoBytes = iso.split('').map((c) => c.charCodeAt(0));
        bytes.set(isoBytes, 12);
    }
    return bytes;
}
/**
 * Tests if ISO is a valid iso code
 */
function isIsoCode(iso) {
    return ISO_REGEX.test(iso);
}
function isoCodeFromHex(code) {
    const iso = (0, utils_1.hexToString)((0, utils_1.bytesToHex)(code));
    if (iso === 'XRP') {
        return null;
    }
    if (isIsoCode(iso)) {
        return iso;
    }
    return null;
}
/**
 * Tests if hex is a valid hex-string
 */
function isHex(hex) {
    return HEX_REGEX.test(hex);
}
/**
 * Tests if a string is a valid representation of a currency
 */
function isStringRepresentation(input) {
    return input.length === 3 || isHex(input);
}
/**
 * Tests if a Uint8Array is a valid representation of a currency
 */
function isBytesArray(bytes) {
    return bytes.byteLength === 20;
}
/**
 * Ensures that a value is a valid representation of a currency
 */
function isValidRepresentation(input) {
    return input instanceof Uint8Array
        ? isBytesArray(input)
        : isStringRepresentation(input);
}
/**
 * Generate bytes from a string or UInt8Array representation of a currency
 */
function bytesFromRepresentation(input) {
    if (!isValidRepresentation(input)) {
        throw new Error(`Unsupported Currency representation: ${input}`);
    }
    return input.length === 3 ? isoToBytes(input) : (0, utils_1.hexToBytes)(input);
}
/**
 * Class defining how to encode and decode Currencies
 */
class Currency extends hash_160_1.Hash160 {
    constructor(byteBuf) {
        super(byteBuf !== null && byteBuf !== void 0 ? byteBuf : Currency.XRP.bytes);
        const hex = (0, utils_1.bytesToHex)(this.bytes);
        if (XRP_HEX_REGEX.test(hex)) {
            this._iso = 'XRP';
        }
        else if (STANDARD_FORMAT_HEX_REGEX.test(hex)) {
            this._iso = isoCodeFromHex(this.bytes.slice(12, 15));
        }
        else {
            this._iso = null;
        }
    }
    /**
     * Return the ISO code of this currency
     *
     * @returns ISO code if it exists, else null
     */
    iso() {
        return this._iso;
    }
    /**
     * Constructs a Currency object
     *
     * @param val Currency object or a string representation of a currency
     */
    static from(value) {
        if (value instanceof Currency) {
            return value;
        }
        if (typeof value === 'string') {
            return new Currency(bytesFromRepresentation(value));
        }
        throw new Error('Cannot construct Currency from value given');
    }
    /**
     * Gets the JSON representation of a currency
     *
     * @returns JSON representation
     */
    toJSON() {
        const iso = this.iso();
        if (iso !== null) {
            return iso;
        }
        return (0, utils_1.bytesToHex)(this.bytes);
    }
}
exports.Currency = Currency;
Currency.XRP = new Currency(new Uint8Array(20));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/hash-128.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/hash-128.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash128 = void 0;
const hash_1 = __webpack_require__(/*! ./hash */ "../../node_modules/ripple-binary-codec/dist/types/hash.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * Hash with a width of 128 bits
 */
class Hash128 extends hash_1.Hash {
    constructor(bytes) {
        if (bytes && bytes.byteLength === 0) {
            bytes = Hash128.ZERO_128.bytes;
        }
        super(bytes !== null && bytes !== void 0 ? bytes : Hash128.ZERO_128.bytes);
    }
    /**
     * Get the hex representation of a hash-128 bytes, allowing unset
     *
     * @returns hex String of this.bytes
     */
    toHex() {
        const hex = (0, utils_1.bytesToHex)(this.toBytes());
        if (/^0+$/.exec(hex)) {
            return '';
        }
        return hex;
    }
}
exports.Hash128 = Hash128;
Hash128.width = 16;
Hash128.ZERO_128 = new Hash128(new Uint8Array(Hash128.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/hash-160.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/hash-160.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash160 = void 0;
const hash_1 = __webpack_require__(/*! ./hash */ "../../node_modules/ripple-binary-codec/dist/types/hash.js");
/**
 * Hash with a width of 160 bits
 */
class Hash160 extends hash_1.Hash {
    constructor(bytes) {
        if (bytes && bytes.byteLength === 0) {
            bytes = Hash160.ZERO_160.bytes;
        }
        super(bytes !== null && bytes !== void 0 ? bytes : Hash160.ZERO_160.bytes);
    }
}
exports.Hash160 = Hash160;
Hash160.width = 20;
Hash160.ZERO_160 = new Hash160(new Uint8Array(Hash160.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/hash-192.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/hash-192.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash192 = void 0;
const hash_1 = __webpack_require__(/*! ./hash */ "../../node_modules/ripple-binary-codec/dist/types/hash.js");
/**
 * Hash with a width of 192 bits
 */
class Hash192 extends hash_1.Hash {
    constructor(bytes) {
        if (bytes && bytes.byteLength === 0) {
            bytes = Hash192.ZERO_192.bytes;
        }
        super(bytes !== null && bytes !== void 0 ? bytes : Hash192.ZERO_192.bytes);
    }
}
exports.Hash192 = Hash192;
Hash192.width = 24;
Hash192.ZERO_192 = new Hash192(new Uint8Array(Hash192.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/hash-256.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/hash-256.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash256 = void 0;
const hash_1 = __webpack_require__(/*! ./hash */ "../../node_modules/ripple-binary-codec/dist/types/hash.js");
/**
 * Hash with a width of 256 bits
 */
class Hash256 extends hash_1.Hash {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : Hash256.ZERO_256.bytes);
    }
}
exports.Hash256 = Hash256;
Hash256.width = 32;
Hash256.ZERO_256 = new Hash256(new Uint8Array(Hash256.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/hash.js":
/*!*****************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/hash.js ***!
  \*****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Hash = void 0;
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const utils_2 = __webpack_require__(/*! ../utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
/**
 * Base class defining how to encode and decode hashes
 */
class Hash extends serialized_type_1.Comparable {
    constructor(bytes) {
        super(bytes);
        if (this.bytes.length !== this.constructor.width) {
            throw new Error(`Invalid Hash length ${this.bytes.byteLength}`);
        }
    }
    /**
     * Construct a Hash object from an existing Hash object or a hex-string
     *
     * @param value A hash object or hex-string of a hash
     */
    static from(value) {
        if (value instanceof this) {
            return value;
        }
        if (typeof value === 'string') {
            return new this((0, utils_1.hexToBytes)(value));
        }
        throw new Error('Cannot construct Hash from given value');
    }
    /**
     * Read a Hash object from a BinaryParser
     *
     * @param parser BinaryParser to read the hash from
     * @param hint length of the bytes to read, optional
     */
    static fromParser(parser, hint) {
        return new this(parser.read(hint !== null && hint !== void 0 ? hint : this.width));
    }
    /**
     * Overloaded operator for comparing two hash objects
     *
     * @param other The Hash to compare this to
     */
    compareTo(other) {
        return (0, utils_2.compare)(this.bytes, this.constructor.from(other).bytes);
    }
    /**
     * @returns the hex-string representation of this Hash
     */
    toString() {
        return this.toHex();
    }
    /**
     * Returns four bits at the specified depth within a hash
     *
     * @param depth The depth of the four bits
     * @returns The number represented by the four bits
     */
    nibblet(depth) {
        const byteIx = depth > 0 ? (depth / 2) | 0 : 0;
        let b = this.bytes[byteIx];
        if (depth % 2 === 0) {
            b = (b & 0xf0) >>> 4;
        }
        else {
            b = b & 0x0f;
        }
        return b;
    }
}
exports.Hash = Hash;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/index.js":
/*!******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/index.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Vector256 = exports.UInt64 = exports.UInt32 = exports.UInt16 = exports.UInt8 = exports.STObject = exports.STArray = exports.PathSet = exports.Hash256 = exports.Hash192 = exports.Hash160 = exports.Hash128 = exports.Currency = exports.Blob = exports.Amount = exports.AccountID = exports.coreTypes = void 0;
const account_id_1 = __webpack_require__(/*! ./account-id */ "../../node_modules/ripple-binary-codec/dist/types/account-id.js");
Object.defineProperty(exports, "AccountID", ({ enumerable: true, get: function () { return account_id_1.AccountID; } }));
const amount_1 = __webpack_require__(/*! ./amount */ "../../node_modules/ripple-binary-codec/dist/types/amount.js");
Object.defineProperty(exports, "Amount", ({ enumerable: true, get: function () { return amount_1.Amount; } }));
const blob_1 = __webpack_require__(/*! ./blob */ "../../node_modules/ripple-binary-codec/dist/types/blob.js");
Object.defineProperty(exports, "Blob", ({ enumerable: true, get: function () { return blob_1.Blob; } }));
const currency_1 = __webpack_require__(/*! ./currency */ "../../node_modules/ripple-binary-codec/dist/types/currency.js");
Object.defineProperty(exports, "Currency", ({ enumerable: true, get: function () { return currency_1.Currency; } }));
const hash_128_1 = __webpack_require__(/*! ./hash-128 */ "../../node_modules/ripple-binary-codec/dist/types/hash-128.js");
Object.defineProperty(exports, "Hash128", ({ enumerable: true, get: function () { return hash_128_1.Hash128; } }));
const hash_160_1 = __webpack_require__(/*! ./hash-160 */ "../../node_modules/ripple-binary-codec/dist/types/hash-160.js");
Object.defineProperty(exports, "Hash160", ({ enumerable: true, get: function () { return hash_160_1.Hash160; } }));
const hash_192_1 = __webpack_require__(/*! ./hash-192 */ "../../node_modules/ripple-binary-codec/dist/types/hash-192.js");
Object.defineProperty(exports, "Hash192", ({ enumerable: true, get: function () { return hash_192_1.Hash192; } }));
const hash_256_1 = __webpack_require__(/*! ./hash-256 */ "../../node_modules/ripple-binary-codec/dist/types/hash-256.js");
Object.defineProperty(exports, "Hash256", ({ enumerable: true, get: function () { return hash_256_1.Hash256; } }));
const issue_1 = __webpack_require__(/*! ./issue */ "../../node_modules/ripple-binary-codec/dist/types/issue.js");
const path_set_1 = __webpack_require__(/*! ./path-set */ "../../node_modules/ripple-binary-codec/dist/types/path-set.js");
Object.defineProperty(exports, "PathSet", ({ enumerable: true, get: function () { return path_set_1.PathSet; } }));
const st_array_1 = __webpack_require__(/*! ./st-array */ "../../node_modules/ripple-binary-codec/dist/types/st-array.js");
Object.defineProperty(exports, "STArray", ({ enumerable: true, get: function () { return st_array_1.STArray; } }));
const st_object_1 = __webpack_require__(/*! ./st-object */ "../../node_modules/ripple-binary-codec/dist/types/st-object.js");
Object.defineProperty(exports, "STObject", ({ enumerable: true, get: function () { return st_object_1.STObject; } }));
const uint_16_1 = __webpack_require__(/*! ./uint-16 */ "../../node_modules/ripple-binary-codec/dist/types/uint-16.js");
Object.defineProperty(exports, "UInt16", ({ enumerable: true, get: function () { return uint_16_1.UInt16; } }));
const uint_32_1 = __webpack_require__(/*! ./uint-32 */ "../../node_modules/ripple-binary-codec/dist/types/uint-32.js");
Object.defineProperty(exports, "UInt32", ({ enumerable: true, get: function () { return uint_32_1.UInt32; } }));
const uint_64_1 = __webpack_require__(/*! ./uint-64 */ "../../node_modules/ripple-binary-codec/dist/types/uint-64.js");
Object.defineProperty(exports, "UInt64", ({ enumerable: true, get: function () { return uint_64_1.UInt64; } }));
const uint_8_1 = __webpack_require__(/*! ./uint-8 */ "../../node_modules/ripple-binary-codec/dist/types/uint-8.js");
Object.defineProperty(exports, "UInt8", ({ enumerable: true, get: function () { return uint_8_1.UInt8; } }));
const vector_256_1 = __webpack_require__(/*! ./vector-256 */ "../../node_modules/ripple-binary-codec/dist/types/vector-256.js");
Object.defineProperty(exports, "Vector256", ({ enumerable: true, get: function () { return vector_256_1.Vector256; } }));
const xchain_bridge_1 = __webpack_require__(/*! ./xchain-bridge */ "../../node_modules/ripple-binary-codec/dist/types/xchain-bridge.js");
const enums_1 = __webpack_require__(/*! ../enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
const coreTypes = {
    AccountID: account_id_1.AccountID,
    Amount: amount_1.Amount,
    Blob: blob_1.Blob,
    Currency: currency_1.Currency,
    Hash128: hash_128_1.Hash128,
    Hash160: hash_160_1.Hash160,
    Hash192: hash_192_1.Hash192,
    Hash256: hash_256_1.Hash256,
    Issue: issue_1.Issue,
    PathSet: path_set_1.PathSet,
    STArray: st_array_1.STArray,
    STObject: st_object_1.STObject,
    UInt8: uint_8_1.UInt8,
    UInt16: uint_16_1.UInt16,
    UInt32: uint_32_1.UInt32,
    UInt64: uint_64_1.UInt64,
    Vector256: vector_256_1.Vector256,
    XChainBridge: xchain_bridge_1.XChainBridge,
};
exports.coreTypes = coreTypes;
// Ensures that the DEFAULT_DEFINITIONS object connects these types to fields for serializing/deserializing
// This is done here instead of in enums/index.ts to avoid a circular dependency
// because some of the above types depend on BinarySerializer which depends on enums/index.ts.
enums_1.DEFAULT_DEFINITIONS.associateTypes(coreTypes);


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/issue.js":
/*!******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/issue.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Issue = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const binary_parser_1 = __webpack_require__(/*! ../serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
const account_id_1 = __webpack_require__(/*! ./account-id */ "../../node_modules/ripple-binary-codec/dist/types/account-id.js");
const currency_1 = __webpack_require__(/*! ./currency */ "../../node_modules/ripple-binary-codec/dist/types/currency.js");
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const hash_192_1 = __webpack_require__(/*! ./hash-192 */ "../../node_modules/ripple-binary-codec/dist/types/hash-192.js");
/**
 * Type guard for Issue Object
 */
function isIssueObject(arg) {
    const keys = Object.keys(arg).sort();
    const isXRP = keys.length === 1 && keys[0] === 'currency';
    const isIOU = keys.length === 2 && keys[0] === 'currency' && keys[1] === 'issuer';
    const isMPT = keys.length === 1 && keys[0] === 'mpt_issuance_id';
    return isXRP || isIOU || isMPT;
}
/**
 * Class for serializing/Deserializing Amounts
 */
class Issue extends serialized_type_1.SerializedType {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : Issue.ZERO_ISSUED_CURRENCY.bytes);
    }
    /**
     * Construct an amount from an IOU or string amount
     *
     * @param value An Amount, object representing an IOU, MPTAmount, or a string
     *     representing an integer amount
     * @returns An Issue object
     */
    static from(value) {
        if (value instanceof Issue) {
            return value;
        }
        if (isIssueObject(value)) {
            if (value.currency) {
                const currency = currency_1.Currency.from(value.currency.toString()).toBytes();
                //IOU case
                if (value.issuer) {
                    const issuer = account_id_1.AccountID.from(value.issuer.toString()).toBytes();
                    return new Issue((0, utils_1.concat)([currency, issuer]));
                }
                //XRP case
                return new Issue(currency);
            }
            // MPT case
            if (value.mpt_issuance_id) {
                const mptIssuanceIdBytes = hash_192_1.Hash192.from(value.mpt_issuance_id.toString()).toBytes();
                return new Issue(mptIssuanceIdBytes);
            }
        }
        throw new Error('Invalid type to construct an Amount');
    }
    /**
     * Read an amount from a BinaryParser
     *
     * @param parser BinaryParser to read the Amount from
     * @param hint The number of bytes to consume from the parser.
     * For an MPT amount, pass 24 (the fixed length for Hash192).
     *
     * @returns An Issue object
     */
    static fromParser(parser, hint) {
        if (hint === hash_192_1.Hash192.width) {
            const mptBytes = parser.read(hash_192_1.Hash192.width);
            return new Issue(mptBytes);
        }
        const currency = parser.read(20);
        if (new currency_1.Currency(currency).toJSON() === 'XRP') {
            return new Issue(currency);
        }
        const currencyAndIssuer = [currency, parser.read(20)];
        return new Issue((0, utils_1.concat)(currencyAndIssuer));
    }
    /**
     * Get the JSON representation of this Amount
     *
     * @returns the JSON interpretation of this.bytes
     */
    toJSON() {
        // If the buffer is exactly 24 bytes, treat it as an MPT amount.
        if (this.toBytes().length === hash_192_1.Hash192.width) {
            return {
                mpt_issuance_id: this.toHex().toUpperCase(),
            };
        }
        const parser = new binary_parser_1.BinaryParser(this.toString());
        const currency = currency_1.Currency.fromParser(parser);
        if (currency.toJSON() === 'XRP') {
            return { currency: currency.toJSON() };
        }
        const issuer = account_id_1.AccountID.fromParser(parser);
        return {
            currency: currency.toJSON(),
            issuer: issuer.toJSON(),
        };
    }
}
exports.Issue = Issue;
Issue.ZERO_ISSUED_CURRENCY = new Issue(new Uint8Array(20));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/path-set.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/path-set.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.PathSet = void 0;
const account_id_1 = __webpack_require__(/*! ./account-id */ "../../node_modules/ripple-binary-codec/dist/types/account-id.js");
const currency_1 = __webpack_require__(/*! ./currency */ "../../node_modules/ripple-binary-codec/dist/types/currency.js");
const binary_parser_1 = __webpack_require__(/*! ../serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * Constants for separating Paths in a PathSet
 */
const PATHSET_END_BYTE = 0x00;
const PATH_SEPARATOR_BYTE = 0xff;
/**
 * Constant for masking types of a Hop
 */
const TYPE_ACCOUNT = 0x01;
const TYPE_CURRENCY = 0x10;
const TYPE_ISSUER = 0x20;
/**
 * TypeGuard for HopObject
 */
function isHopObject(arg) {
    return (arg.issuer !== undefined ||
        arg.account !== undefined ||
        arg.currency !== undefined);
}
/**
 * TypeGuard for PathSet
 */
function isPathSet(arg) {
    return ((Array.isArray(arg) && arg.length === 0) ||
        (Array.isArray(arg) && Array.isArray(arg[0]) && arg[0].length === 0) ||
        (Array.isArray(arg) && Array.isArray(arg[0]) && isHopObject(arg[0][0])));
}
/**
 * Serialize and Deserialize a Hop
 */
class Hop extends serialized_type_1.SerializedType {
    /**
     * Create a Hop from a HopObject
     *
     * @param value Either a hop or HopObject to create a hop with
     * @returns a Hop
     */
    static from(value) {
        if (value instanceof Hop) {
            return value;
        }
        const bytes = [Uint8Array.from([0])];
        if (value.account) {
            bytes.push(account_id_1.AccountID.from(value.account).toBytes());
            bytes[0][0] |= TYPE_ACCOUNT;
        }
        if (value.currency) {
            bytes.push(currency_1.Currency.from(value.currency).toBytes());
            bytes[0][0] |= TYPE_CURRENCY;
        }
        if (value.issuer) {
            bytes.push(account_id_1.AccountID.from(value.issuer).toBytes());
            bytes[0][0] |= TYPE_ISSUER;
        }
        return new Hop((0, utils_1.concat)(bytes));
    }
    /**
     * Construct a Hop from a BinaryParser
     *
     * @param parser BinaryParser to read the Hop from
     * @returns a Hop
     */
    static fromParser(parser) {
        const type = parser.readUInt8();
        const bytes = [Uint8Array.from([type])];
        if (type & TYPE_ACCOUNT) {
            bytes.push(parser.read(account_id_1.AccountID.width));
        }
        if (type & TYPE_CURRENCY) {
            bytes.push(parser.read(currency_1.Currency.width));
        }
        if (type & TYPE_ISSUER) {
            bytes.push(parser.read(account_id_1.AccountID.width));
        }
        return new Hop((0, utils_1.concat)(bytes));
    }
    /**
     * Get the JSON interpretation of this hop
     *
     * @returns a HopObject, an JS object with optional account, issuer, and currency
     */
    toJSON() {
        const hopParser = new binary_parser_1.BinaryParser((0, utils_1.bytesToHex)(this.bytes));
        const type = hopParser.readUInt8();
        let account, currency, issuer;
        if (type & TYPE_ACCOUNT) {
            account = account_id_1.AccountID.fromParser(hopParser).toJSON();
        }
        if (type & TYPE_CURRENCY) {
            currency = currency_1.Currency.fromParser(hopParser).toJSON();
        }
        if (type & TYPE_ISSUER) {
            issuer = account_id_1.AccountID.fromParser(hopParser).toJSON();
        }
        const result = {};
        if (account) {
            result.account = account;
        }
        if (issuer) {
            result.issuer = issuer;
        }
        if (currency) {
            result.currency = currency;
        }
        return result;
    }
    /**
     * get a number representing the type of this hop
     *
     * @returns a number to be bitwise and-ed with TYPE_ constants to describe the types in the hop
     */
    type() {
        return this.bytes[0];
    }
}
/**
 * Class for serializing/deserializing Paths
 */
class Path extends serialized_type_1.SerializedType {
    /**
     * construct a Path from an array of Hops
     *
     * @param value Path or array of HopObjects to construct a Path
     * @returns the Path
     */
    static from(value) {
        if (value instanceof Path) {
            return value;
        }
        const bytes = [];
        value.forEach((hop) => {
            bytes.push(Hop.from(hop).toBytes());
        });
        return new Path((0, utils_1.concat)(bytes));
    }
    /**
     * Read a Path from a BinaryParser
     *
     * @param parser BinaryParser to read Path from
     * @returns the Path represented by the bytes read from the BinaryParser
     */
    static fromParser(parser) {
        const bytes = [];
        while (!parser.end()) {
            bytes.push(Hop.fromParser(parser).toBytes());
            if (parser.peek() === PATHSET_END_BYTE ||
                parser.peek() === PATH_SEPARATOR_BYTE) {
                break;
            }
        }
        return new Path((0, utils_1.concat)(bytes));
    }
    /**
     * Get the JSON representation of this Path
     *
     * @returns an Array of HopObject constructed from this.bytes
     */
    toJSON() {
        const json = [];
        const pathParser = new binary_parser_1.BinaryParser(this.toString());
        while (!pathParser.end()) {
            json.push(Hop.fromParser(pathParser).toJSON());
        }
        return json;
    }
}
/**
 * Deserialize and Serialize the PathSet type
 */
class PathSet extends serialized_type_1.SerializedType {
    /**
     * Construct a PathSet from an Array of Arrays representing paths
     *
     * @param value A PathSet or Array of Array of HopObjects
     * @returns the PathSet constructed from value
     */
    static from(value) {
        if (value instanceof PathSet) {
            return value;
        }
        if (isPathSet(value)) {
            const bytes = [];
            value.forEach((path) => {
                bytes.push(Path.from(path).toBytes());
                bytes.push(Uint8Array.from([PATH_SEPARATOR_BYTE]));
            });
            bytes[bytes.length - 1] = Uint8Array.from([PATHSET_END_BYTE]);
            return new PathSet((0, utils_1.concat)(bytes));
        }
        throw new Error('Cannot construct PathSet from given value');
    }
    /**
     * Construct a PathSet from a BinaryParser
     *
     * @param parser A BinaryParser to read PathSet from
     * @returns the PathSet read from parser
     */
    static fromParser(parser) {
        const bytes = [];
        while (!parser.end()) {
            bytes.push(Path.fromParser(parser).toBytes());
            bytes.push(parser.read(1));
            if (bytes[bytes.length - 1][0] == PATHSET_END_BYTE) {
                break;
            }
        }
        return new PathSet((0, utils_1.concat)(bytes));
    }
    /**
     * Get the JSON representation of this PathSet
     *
     * @returns an Array of Array of HopObjects, representing this PathSet
     */
    toJSON() {
        const json = [];
        const pathParser = new binary_parser_1.BinaryParser(this.toString());
        while (!pathParser.end()) {
            json.push(Path.fromParser(pathParser).toJSON());
            pathParser.skip(1);
        }
        return json;
    }
}
exports.PathSet = PathSet;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js":
/*!****************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/serialized-type.js ***!
  \****************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Comparable = exports.SerializedType = void 0;
const binary_serializer_1 = __webpack_require__(/*! ../serdes/binary-serializer */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * The base class for all binary-codec types
 */
class SerializedType {
    constructor(bytes) {
        this.bytes = new Uint8Array(0);
        this.bytes = bytes !== null && bytes !== void 0 ? bytes : new Uint8Array(0);
    }
    static fromParser(parser, hint) {
        throw new Error('fromParser not implemented');
        return this.fromParser(parser, hint);
    }
    static from(value) {
        throw new Error('from not implemented');
        return this.from(value);
    }
    /**
     * Write the bytes representation of a SerializedType to a BytesList
     *
     * @param list The BytesList to write SerializedType bytes to
     */
    toBytesSink(list) {
        list.put(this.bytes);
    }
    /**
     * Get the hex representation of a SerializedType's bytes
     *
     * @returns hex String of this.bytes
     */
    toHex() {
        return (0, utils_1.bytesToHex)(this.toBytes());
    }
    /**
     * Get the bytes representation of a SerializedType
     *
     * @returns A Uint8Array of the bytes
     */
    toBytes() {
        if (this.bytes) {
            return this.bytes;
        }
        const bytes = new binary_serializer_1.BytesList();
        this.toBytesSink(bytes);
        return bytes.toBytes();
    }
    /**
     * Return the JSON representation of a SerializedType
     *
     * @param _definitions rippled definitions used to parse the values of transaction types and such.
     *                          Unused in default, but used in STObject, STArray
     *                          Can be customized for sidechains and amendments.
     * @returns any type, if not overloaded returns hexString representation of bytes
     */
    toJSON(_definitions, _fieldName) {
        return this.toHex();
    }
    /**
     * @returns hexString representation of this.bytes
     */
    toString() {
        return this.toHex();
    }
}
exports.SerializedType = SerializedType;
/**
 * Base class for SerializedTypes that are comparable.
 *
 * @template T - What types you want to allow comparisons between. You must specify all types. Primarily used to allow
 * comparisons between built-in types (like `string`) and SerializedType subclasses (like `Hash`).
 *
 * Ex. `class Hash extends Comparable<Hash | string>`
 */
class Comparable extends SerializedType {
    lt(other) {
        return this.compareTo(other) < 0;
    }
    eq(other) {
        return this.compareTo(other) === 0;
    }
    gt(other) {
        return this.compareTo(other) > 0;
    }
    gte(other) {
        return this.compareTo(other) > -1;
    }
    lte(other) {
        return this.compareTo(other) < 1;
    }
    /**
     * Overload this method to define how two Comparable SerializedTypes are compared
     *
     * @param other The comparable object to compare this to
     * @returns A number denoting the relationship of this and other
     */
    compareTo(other) {
        throw new Error(`cannot compare ${this.toString()} and ${other.toString()}`);
    }
}
exports.Comparable = Comparable;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/st-array.js":
/*!*********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/st-array.js ***!
  \*********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.STArray = void 0;
const enums_1 = __webpack_require__(/*! ../enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const st_object_1 = __webpack_require__(/*! ./st-object */ "../../node_modules/ripple-binary-codec/dist/types/st-object.js");
const binary_parser_1 = __webpack_require__(/*! ../serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const ARRAY_END_MARKER = Uint8Array.from([0xf1]);
const ARRAY_END_MARKER_NAME = 'ArrayEndMarker';
const OBJECT_END_MARKER = Uint8Array.from([0xe1]);
/**
 * TypeGuard for Array<JsonObject>
 */
function isObjects(args) {
    return (Array.isArray(args) &&
        args.every((arg) => typeof arg === 'object' &&
            Object.keys(arg).length === 1 &&
            typeof Object.values(arg)[0] === 'object'));
}
/**
 * Class for serializing and deserializing Arrays of Objects
 */
class STArray extends serialized_type_1.SerializedType {
    /**
     * Construct an STArray from a BinaryParser
     *
     * @param parser BinaryParser to parse an STArray from
     * @returns An STArray Object
     */
    static fromParser(parser) {
        const bytes = [];
        while (!parser.end()) {
            const field = parser.readField();
            if (field.name === ARRAY_END_MARKER_NAME) {
                break;
            }
            bytes.push(field.header, parser.readFieldValue(field).toBytes(), OBJECT_END_MARKER);
        }
        bytes.push(ARRAY_END_MARKER);
        return new STArray((0, utils_1.concat)(bytes));
    }
    /**
     * Construct an STArray from an Array of JSON Objects
     *
     * @param value STArray or Array of Objects to parse into an STArray
     * @param definitions optional, types and values to use to encode/decode a transaction
     * @returns An STArray object
     */
    static from(value, definitions = enums_1.DEFAULT_DEFINITIONS) {
        if (value instanceof STArray) {
            return value;
        }
        if (isObjects(value)) {
            const bytes = [];
            value.forEach((obj) => {
                bytes.push(st_object_1.STObject.from(obj, undefined, definitions).toBytes());
            });
            bytes.push(ARRAY_END_MARKER);
            return new STArray((0, utils_1.concat)(bytes));
        }
        throw new Error('Cannot construct STArray from value given');
    }
    /**
     * Return the JSON representation of this.bytes
     *
     * @param definitions optional, types and values to use to encode/decode a transaction
     * @returns An Array of JSON objects
     */
    toJSON(definitions = enums_1.DEFAULT_DEFINITIONS) {
        const result = [];
        const arrayParser = new binary_parser_1.BinaryParser(this.toString(), definitions);
        while (!arrayParser.end()) {
            const field = arrayParser.readField();
            if (field.name === ARRAY_END_MARKER_NAME) {
                break;
            }
            const outer = {};
            outer[field.name] = st_object_1.STObject.fromParser(arrayParser).toJSON(definitions);
            result.push(outer);
        }
        return result;
    }
}
exports.STArray = STArray;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/st-object.js":
/*!**********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/st-object.js ***!
  \**********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.STObject = void 0;
const enums_1 = __webpack_require__(/*! ../enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const binary_parser_1 = __webpack_require__(/*! ../serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
const binary_serializer_1 = __webpack_require__(/*! ../serdes/binary-serializer */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js");
const st_array_1 = __webpack_require__(/*! ./st-array */ "../../node_modules/ripple-binary-codec/dist/types/st-array.js");
const uint_64_1 = __webpack_require__(/*! ./uint-64 */ "../../node_modules/ripple-binary-codec/dist/types/uint-64.js");
const OBJECT_END_MARKER_BYTE = Uint8Array.from([0xe1]);
const OBJECT_END_MARKER = 'ObjectEndMarker';
const ST_OBJECT = 'STObject';
const DESTINATION = 'Destination';
const ACCOUNT = 'Account';
const SOURCE_TAG = 'SourceTag';
const DEST_TAG = 'DestinationTag';
/**
 * Break down an X-Address into an account and a tag
 *
 * @param field Name of field
 * @param xAddress X-Address corresponding to the field
 */
function handleXAddress(field, xAddress) {
    const decoded = (0, ripple_address_codec_1.xAddressToClassicAddress)(xAddress);
    let tagName;
    if (field === DESTINATION)
        tagName = DEST_TAG;
    else if (field === ACCOUNT)
        tagName = SOURCE_TAG;
    else if (decoded.tag !== false)
        throw new Error(`${field} cannot have an associated tag`);
    return decoded.tag !== false
        ? { [field]: decoded.classicAddress, [tagName]: decoded.tag }
        : { [field]: decoded.classicAddress };
}
/**
 * Validate that two objects don't both have the same tag fields
 *
 * @param obj1 First object to check for tags
 * @param obj2 Second object to check for tags
 * @throws When both objects have SourceTag or DestinationTag
 */
function checkForDuplicateTags(obj1, obj2) {
    if (!(obj1[SOURCE_TAG] === undefined || obj2[SOURCE_TAG] === undefined))
        throw new Error('Cannot have Account X-Address and SourceTag');
    if (!(obj1[DEST_TAG] === undefined || obj2[DEST_TAG] === undefined))
        throw new Error('Cannot have Destination X-Address and DestinationTag');
}
/**
 * Class for Serializing/Deserializing objects
 */
class STObject extends serialized_type_1.SerializedType {
    /**
     * Construct a STObject from a BinaryParser
     *
     * @param parser BinaryParser to read STObject from
     * @returns A STObject object
     */
    static fromParser(parser) {
        const list = new binary_serializer_1.BytesList();
        const bytes = new binary_serializer_1.BinarySerializer(list);
        while (!parser.end()) {
            const field = parser.readField();
            if (field.name === OBJECT_END_MARKER) {
                break;
            }
            const associatedValue = parser.readFieldValue(field);
            bytes.writeFieldAndValue(field, associatedValue);
            if (field.type.name === ST_OBJECT) {
                bytes.put(OBJECT_END_MARKER_BYTE);
            }
        }
        return new STObject(list.toBytes());
    }
    /**
     * Construct a STObject from a JSON object
     *
     * @param value An object to include
     * @param filter optional, denote which field to include in serialized object
     * @param definitions optional, types and values to use to encode/decode a transaction
     * @returns a STObject object
     */
    static from(value, filter, definitions = enums_1.DEFAULT_DEFINITIONS) {
        if (value instanceof STObject) {
            return value;
        }
        const list = new binary_serializer_1.BytesList();
        const bytes = new binary_serializer_1.BinarySerializer(list);
        let isUnlModify = false;
        const xAddressDecoded = Object.entries(value).reduce((acc, [key, val]) => {
            let handled = undefined;
            if (val && (0, ripple_address_codec_1.isValidXAddress)(val.toString())) {
                handled = handleXAddress(key, val.toString());
                checkForDuplicateTags(handled, value);
            }
            return Object.assign(acc, handled !== null && handled !== void 0 ? handled : { [key]: val });
        }, {});
        function isValidFieldInstance(f) {
            return (f !== undefined &&
                xAddressDecoded[f.name] !== undefined &&
                f.isSerialized);
        }
        let sorted = Object.keys(xAddressDecoded)
            .map((f) => {
            if (!(f in definitions.field)) {
                if (f[0] === f[0].toLowerCase())
                    return undefined;
                throw new Error(`Field ${f} is not defined in the definitions`);
            }
            return definitions.field[f];
        })
            .filter(isValidFieldInstance)
            .sort((a, b) => {
            return a.ordinal - b.ordinal;
        });
        if (filter !== undefined) {
            sorted = sorted.filter(filter);
        }
        sorted.forEach((field) => {
            const associatedValue = field.type.name === ST_OBJECT
                ? this.from(xAddressDecoded[field.name], undefined, definitions)
                : field.type.name === 'STArray'
                    ? st_array_1.STArray.from(xAddressDecoded[field.name], definitions)
                    : field.type.name === 'UInt64'
                        ? uint_64_1.UInt64.from(xAddressDecoded[field.name], field.name)
                        : field.associatedType.from(xAddressDecoded[field.name]);
            if (associatedValue == undefined) {
                throw new TypeError(`Unable to interpret "${field.name}: ${xAddressDecoded[field.name]}".`);
            }
            if (associatedValue.name === 'UNLModify') {
                // triggered when the TransactionType field has a value of 'UNLModify'
                isUnlModify = true;
            }
            // true when in the UNLModify pseudotransaction (after the transaction type has been processed) and working with the
            // Account field
            // The Account field must not be a part of the UNLModify pseudotransaction encoding, due to a bug in rippled
            const isUnlModifyWorkaround = field.name == 'Account' && isUnlModify;
            bytes.writeFieldAndValue(field, associatedValue, isUnlModifyWorkaround);
            if (field.type.name === ST_OBJECT) {
                bytes.put(OBJECT_END_MARKER_BYTE);
            }
        });
        return new STObject(list.toBytes());
    }
    /**
     * Get the JSON interpretation of this.bytes
     * @param definitions rippled definitions used to parse the values of transaction types and such.
     *                          Can be customized for sidechains and amendments.
     * @returns a JSON object
     */
    toJSON(definitions) {
        const objectParser = new binary_parser_1.BinaryParser(this.toString(), definitions);
        const accumulator = {};
        while (!objectParser.end()) {
            const field = objectParser.readField();
            if (field.name === OBJECT_END_MARKER) {
                break;
            }
            accumulator[field.name] = objectParser
                .readFieldValue(field)
                .toJSON(definitions, field.name);
        }
        return accumulator;
    }
}
exports.STObject = STObject;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/uint-16.js":
/*!********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/uint-16.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.UInt16 = void 0;
const uint_1 = __webpack_require__(/*! ./uint */ "../../node_modules/ripple-binary-codec/dist/types/uint.js");
const utils_1 = __webpack_require__(/*! ../utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
/**
 * Derived UInt class for serializing/deserializing 16 bit UInt
 */
class UInt16 extends uint_1.UInt {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : UInt16.defaultUInt16.bytes);
    }
    static fromParser(parser) {
        return new UInt16(parser.read(UInt16.width));
    }
    /**
     * Construct a UInt16 object from a number
     *
     * @param val UInt16 object or number
     */
    static from(val) {
        if (val instanceof UInt16) {
            return val;
        }
        if (typeof val === 'number') {
            UInt16.checkUintRange(val, 0, 0xffff);
            const buf = new Uint8Array(UInt16.width);
            (0, utils_1.writeUInt16BE)(buf, val, 0);
            return new UInt16(buf);
        }
        throw new Error('Can not construct UInt16 with given value');
    }
    /**
     * get the value of a UInt16 object
     *
     * @returns the number represented by this.bytes
     */
    valueOf() {
        return parseInt((0, utils_1.readUInt16BE)(this.bytes, 0));
    }
}
exports.UInt16 = UInt16;
UInt16.width = 16 / 8; // 2
UInt16.defaultUInt16 = new UInt16(new Uint8Array(UInt16.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/uint-32.js":
/*!********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/uint-32.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.UInt32 = void 0;
const uint_1 = __webpack_require__(/*! ./uint */ "../../node_modules/ripple-binary-codec/dist/types/uint.js");
const utils_1 = __webpack_require__(/*! ../utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
/**
 * Derived UInt class for serializing/deserializing 32 bit UInt
 */
class UInt32 extends uint_1.UInt {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : UInt32.defaultUInt32.bytes);
    }
    static fromParser(parser) {
        return new UInt32(parser.read(UInt32.width));
    }
    /**
     * Construct a UInt32 object from a number
     *
     * @param val UInt32 object or number
     */
    static from(val) {
        if (val instanceof UInt32) {
            return val;
        }
        const buf = new Uint8Array(UInt32.width);
        if (typeof val === 'string') {
            const num = Number.parseInt(val);
            (0, utils_1.writeUInt32BE)(buf, num, 0);
            return new UInt32(buf);
        }
        if (typeof val === 'number') {
            UInt32.checkUintRange(val, 0, 0xffffffff);
            (0, utils_1.writeUInt32BE)(buf, val, 0);
            return new UInt32(buf);
        }
        throw new Error('Cannot construct UInt32 from given value');
    }
    /**
     * get the value of a UInt32 object
     *
     * @returns the number represented by this.bytes
     */
    valueOf() {
        return parseInt((0, utils_1.readUInt32BE)(this.bytes, 0), 10);
    }
}
exports.UInt32 = UInt32;
UInt32.width = 32 / 8; // 4
UInt32.defaultUInt32 = new UInt32(new Uint8Array(UInt32.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/uint-64.js":
/*!********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/uint-64.js ***!
  \********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.UInt64 = void 0;
const uint_1 = __webpack_require__(/*! ./uint */ "../../node_modules/ripple-binary-codec/dist/types/uint.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const utils_2 = __webpack_require__(/*! ../utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
const enums_1 = __webpack_require__(/*! ../enums */ "../../node_modules/ripple-binary-codec/dist/enums/index.js");
const HEX_REGEX = /^[a-fA-F0-9]{1,16}$/;
const BASE10_REGEX = /^[0-9]{1,20}$/;
const mask = BigInt(0x00000000ffffffff);
function useBase10(fieldName) {
    return (fieldName === 'MaximumAmount' ||
        fieldName === 'OutstandingAmount' ||
        fieldName === 'MPTAmount');
}
/**
 * Derived UInt class for serializing/deserializing 64 bit UInt
 */
class UInt64 extends uint_1.UInt {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : UInt64.defaultUInt64.bytes);
    }
    static fromParser(parser) {
        return new UInt64(parser.read(UInt64.width));
    }
    /**
     * Construct a UInt64 object
     *
     * @param val A UInt64, hex-string, bigInt, or number
     * @returns A UInt64 object
     */
    static from(val, fieldName = '') {
        if (val instanceof UInt64) {
            return val;
        }
        let buf = new Uint8Array(UInt64.width);
        if (typeof val === 'number') {
            if (val < 0) {
                throw new Error('value must be an unsigned integer');
            }
            const number = BigInt(val);
            const intBuf = [new Uint8Array(4), new Uint8Array(4)];
            (0, utils_2.writeUInt32BE)(intBuf[0], Number(number >> BigInt(32)), 0);
            (0, utils_2.writeUInt32BE)(intBuf[1], Number(number & BigInt(mask)), 0);
            return new UInt64((0, utils_1.concat)(intBuf));
        }
        if (typeof val === 'string') {
            if (useBase10(fieldName)) {
                if (!BASE10_REGEX.test(val)) {
                    throw new Error(`${fieldName} ${val} is not a valid base 10 string`);
                }
                val = BigInt(val).toString(16);
            }
            if (typeof val === 'string' && !HEX_REGEX.test(val)) {
                throw new Error(`${val} is not a valid hex-string`);
            }
            const strBuf = val.padStart(16, '0');
            buf = (0, utils_1.hexToBytes)(strBuf);
            return new UInt64(buf);
        }
        if (typeof val === 'bigint') {
            const intBuf = [new Uint8Array(4), new Uint8Array(4)];
            (0, utils_2.writeUInt32BE)(intBuf[0], Number(Number(val >> BigInt(32))), 0);
            (0, utils_2.writeUInt32BE)(intBuf[1], Number(val & BigInt(mask)), 0);
            return new UInt64((0, utils_1.concat)(intBuf));
        }
        throw new Error('Cannot construct UInt64 from given value');
    }
    /**
     * The JSON representation of a UInt64 object
     *
     * @returns a hex-string
     */
    toJSON(_definitions = enums_1.DEFAULT_DEFINITIONS, fieldName = '') {
        const hexString = (0, utils_1.bytesToHex)(this.bytes);
        if (useBase10(fieldName)) {
            return BigInt('0x' + hexString).toString(10);
        }
        return hexString;
    }
    /**
     * Get the value of the UInt64
     *
     * @returns the number represented buy this.bytes
     */
    valueOf() {
        const msb = BigInt((0, utils_2.readUInt32BE)(this.bytes.slice(0, 4), 0));
        const lsb = BigInt((0, utils_2.readUInt32BE)(this.bytes.slice(4), 0));
        return (msb << BigInt(32)) | lsb;
    }
    /**
     * Get the bytes representation of the UInt64 object
     *
     * @returns 8 bytes representing the UInt64
     */
    toBytes() {
        return this.bytes;
    }
}
exports.UInt64 = UInt64;
UInt64.width = 64 / 8; // 8
UInt64.defaultUInt64 = new UInt64(new Uint8Array(UInt64.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/uint-8.js":
/*!*******************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/uint-8.js ***!
  \*******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.UInt8 = void 0;
const uint_1 = __webpack_require__(/*! ./uint */ "../../node_modules/ripple-binary-codec/dist/types/uint.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const utils_2 = __webpack_require__(/*! ../utils */ "../../node_modules/ripple-binary-codec/dist/utils.js");
/**
 * Derived UInt class for serializing/deserializing 8 bit UInt
 */
class UInt8 extends uint_1.UInt {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : UInt8.defaultUInt8.bytes);
    }
    static fromParser(parser) {
        return new UInt8(parser.read(UInt8.width));
    }
    /**
     * Construct a UInt8 object from a number
     *
     * @param val UInt8 object or number
     */
    static from(val) {
        if (val instanceof UInt8) {
            return val;
        }
        if (typeof val === 'number') {
            UInt8.checkUintRange(val, 0, 0xff);
            const buf = new Uint8Array(UInt8.width);
            (0, utils_2.writeUInt8)(buf, val, 0);
            return new UInt8(buf);
        }
        throw new Error('Cannot construct UInt8 from given value');
    }
    /**
     * get the value of a UInt8 object
     *
     * @returns the number represented by this.bytes
     */
    valueOf() {
        return parseInt((0, utils_1.bytesToHex)(this.bytes), 16);
    }
}
exports.UInt8 = UInt8;
UInt8.width = 8 / 8; // 1
UInt8.defaultUInt8 = new UInt8(new Uint8Array(UInt8.width));


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/uint.js":
/*!*****************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/uint.js ***!
  \*****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.UInt = void 0;
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
/**
 * Compare numbers and bigInts n1 and n2
 *
 * @param n1 First object to compare
 * @param n2 Second object to compare
 * @returns -1, 0, or 1, depending on how the two objects compare
 */
function compare(n1, n2) {
    return n1 < n2 ? -1 : n1 == n2 ? 0 : 1;
}
/**
 * Base class for serializing and deserializing unsigned integers.
 */
class UInt extends serialized_type_1.Comparable {
    constructor(bytes) {
        super(bytes);
    }
    /**
     * Overload of compareTo for Comparable
     *
     * @param other other UInt to compare this to
     * @returns -1, 0, or 1 depending on how the objects relate to each other
     */
    compareTo(other) {
        return compare(this.valueOf(), other.valueOf());
    }
    /**
     * Convert a UInt object to JSON
     *
     * @returns number or string represented by this.bytes
     */
    toJSON() {
        const val = this.valueOf();
        return typeof val === 'number' ? val : val.toString();
    }
    static checkUintRange(val, min, max) {
        if (val < min || val > max) {
            throw new Error(`Invalid ${this.constructor.name}: ${val} must be >= ${min} and <= ${max}`);
        }
    }
}
exports.UInt = UInt;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/vector-256.js":
/*!***********************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/vector-256.js ***!
  \***********************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Vector256 = void 0;
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const hash_256_1 = __webpack_require__(/*! ./hash-256 */ "../../node_modules/ripple-binary-codec/dist/types/hash-256.js");
const binary_serializer_1 = __webpack_require__(/*! ../serdes/binary-serializer */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-serializer.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * TypeGuard for Array<string>
 */
function isStrings(arg) {
    return Array.isArray(arg) && (arg.length === 0 || typeof arg[0] === 'string');
}
/**
 * Class for serializing and deserializing vectors of Hash256
 */
class Vector256 extends serialized_type_1.SerializedType {
    constructor(bytes) {
        super(bytes);
    }
    /**
     * Construct a Vector256 from a BinaryParser
     *
     * @param parser BinaryParser to
     * @param hint length of the vector, in bytes, optional
     * @returns a Vector256 object
     */
    static fromParser(parser, hint) {
        const bytesList = new binary_serializer_1.BytesList();
        const bytes = hint !== null && hint !== void 0 ? hint : parser.size();
        const hashes = bytes / 32;
        for (let i = 0; i < hashes; i++) {
            hash_256_1.Hash256.fromParser(parser).toBytesSink(bytesList);
        }
        return new Vector256(bytesList.toBytes());
    }
    /**
     * Construct a Vector256 object from an array of hashes
     *
     * @param value A Vector256 object or array of hex-strings representing Hash256's
     * @returns a Vector256 object
     */
    static from(value) {
        if (value instanceof Vector256) {
            return value;
        }
        if (isStrings(value)) {
            const bytesList = new binary_serializer_1.BytesList();
            value.forEach((hash) => {
                hash_256_1.Hash256.from(hash).toBytesSink(bytesList);
            });
            return new Vector256(bytesList.toBytes());
        }
        throw new Error('Cannot construct Vector256 from given value');
    }
    /**
     * Return an Array of hex-strings represented by this.bytes
     *
     * @returns An Array of strings representing the Hash256 objects
     */
    toJSON() {
        if (this.bytes.byteLength % 32 !== 0) {
            throw new Error('Invalid bytes for Vector256');
        }
        const result = [];
        for (let i = 0; i < this.bytes.byteLength; i += 32) {
            result.push((0, utils_1.bytesToHex)(this.bytes.slice(i, i + 32)));
        }
        return result;
    }
}
exports.Vector256 = Vector256;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/types/xchain-bridge.js":
/*!**************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/types/xchain-bridge.js ***!
  \**************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.XChainBridge = void 0;
const binary_parser_1 = __webpack_require__(/*! ../serdes/binary-parser */ "../../node_modules/ripple-binary-codec/dist/serdes/binary-parser.js");
const account_id_1 = __webpack_require__(/*! ./account-id */ "../../node_modules/ripple-binary-codec/dist/types/account-id.js");
const serialized_type_1 = __webpack_require__(/*! ./serialized-type */ "../../node_modules/ripple-binary-codec/dist/types/serialized-type.js");
const issue_1 = __webpack_require__(/*! ./issue */ "../../node_modules/ripple-binary-codec/dist/types/issue.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
/**
 * Type guard for XChainBridgeObject
 */
function isXChainBridgeObject(arg) {
    const keys = Object.keys(arg).sort();
    return (keys.length === 4 &&
        keys[0] === 'IssuingChainDoor' &&
        keys[1] === 'IssuingChainIssue' &&
        keys[2] === 'LockingChainDoor' &&
        keys[3] === 'LockingChainIssue');
}
/**
 * Class for serializing/deserializing XChainBridges
 */
class XChainBridge extends serialized_type_1.SerializedType {
    constructor(bytes) {
        super(bytes !== null && bytes !== void 0 ? bytes : XChainBridge.ZERO_XCHAIN_BRIDGE.bytes);
    }
    /**
     * Construct a cross-chain bridge from a JSON
     *
     * @param value XChainBridge or JSON to parse into an XChainBridge
     * @returns An XChainBridge object
     */
    static from(value) {
        if (value instanceof XChainBridge) {
            return value;
        }
        if (!isXChainBridgeObject(value)) {
            throw new Error('Invalid type to construct an XChainBridge');
        }
        const bytes = [];
        this.TYPE_ORDER.forEach((item) => {
            const { name, type } = item;
            if (type === account_id_1.AccountID) {
                bytes.push(Uint8Array.from([0x14]));
            }
            const object = type.from(value[name]);
            bytes.push(object.toBytes());
        });
        return new XChainBridge((0, utils_1.concat)(bytes));
    }
    /**
     * Read an XChainBridge from a BinaryParser
     *
     * @param parser BinaryParser to read the XChainBridge from
     * @returns An XChainBridge object
     */
    static fromParser(parser) {
        const bytes = [];
        this.TYPE_ORDER.forEach((item) => {
            const { type } = item;
            if (type === account_id_1.AccountID) {
                parser.skip(1);
                bytes.push(Uint8Array.from([0x14]));
            }
            const object = type.fromParser(parser);
            bytes.push(object.toBytes());
        });
        return new XChainBridge((0, utils_1.concat)(bytes));
    }
    /**
     * Get the JSON representation of this XChainBridge
     *
     * @returns the JSON interpretation of this.bytes
     */
    toJSON() {
        const parser = new binary_parser_1.BinaryParser(this.toString());
        const json = {};
        XChainBridge.TYPE_ORDER.forEach((item) => {
            const { name, type } = item;
            if (type === account_id_1.AccountID) {
                parser.skip(1);
            }
            const object = type.fromParser(parser).toJSON();
            json[name] = object;
        });
        return json;
    }
}
exports.XChainBridge = XChainBridge;
XChainBridge.ZERO_XCHAIN_BRIDGE = new XChainBridge((0, utils_1.concat)([
    Uint8Array.from([0x14]),
    new Uint8Array(40),
    Uint8Array.from([0x14]),
    new Uint8Array(40),
]));
XChainBridge.TYPE_ORDER = [
    { name: 'LockingChainDoor', type: account_id_1.AccountID },
    { name: 'LockingChainIssue', type: issue_1.Issue },
    { name: 'IssuingChainDoor', type: account_id_1.AccountID },
    { name: 'IssuingChainIssue', type: issue_1.Issue },
];


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/utils.js":
/*!************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/utils.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.compare = exports.equal = exports.readUInt32BE = exports.readUInt16BE = exports.writeUInt32BE = exports.writeUInt16BE = exports.writeUInt8 = void 0;
/**
 * Writes value to array at the specified offset. The value must be a valid unsigned 8-bit integer.
 * @param array Uint8Array to be written to
 * @param value Number to be written to array.
 * @param offset plus the number of bytes written.
 */
function writeUInt8(array, value, offset) {
    value = Number(value);
    array[offset] = value;
}
exports.writeUInt8 = writeUInt8;
/**
 * Writes value to array at the specified offset as big-endian. The value must be a valid unsigned 16-bit integer.
 * @param array Uint8Array to be written to
 * @param value Number to be written to array.
 * @param offset plus the number of bytes written.
 */
function writeUInt16BE(array, value, offset) {
    value = Number(value);
    array[offset] = value >>> 8;
    array[offset + 1] = value;
}
exports.writeUInt16BE = writeUInt16BE;
/**
 * Writes value to array at the specified offset as big-endian. The value must be a valid unsigned 32-bit integer.
 * @param array Uint8Array to be written to
 * @param value Number to be written to array.
 * @param offset plus the number of bytes written.
 */
function writeUInt32BE(array, value, offset) {
    array[offset] = (value >>> 24) & 0xff;
    array[offset + 1] = (value >>> 16) & 0xff;
    array[offset + 2] = (value >>> 8) & 0xff;
    array[offset + 3] = value & 0xff;
}
exports.writeUInt32BE = writeUInt32BE;
/**
 * Reads an unsigned, big-endian 16-bit integer from the array at the specified offset.
 * @param array Uint8Array to read
 * @param offset Number of bytes to skip before starting to read. Must satisfy 0 <= offset <= buf.length - 2
 */
function readUInt16BE(array, offset) {
    return new DataView(array.buffer).getUint16(offset, false).toString(10);
}
exports.readUInt16BE = readUInt16BE;
/**
 * Reads an unsigned, big-endian 16-bit integer from the array at the specified offset.
 * @param array Uint8Array to read
 * @param offset Number of bytes to skip before starting to read. Must satisfy 0 <= offset <= buf.length - 4
 */
function readUInt32BE(array, offset) {
    return new DataView(array.buffer).getUint32(offset, false).toString(10);
}
exports.readUInt32BE = readUInt32BE;
/**
 * Compares two Uint8Array or ArrayBuffers
 * @param a first array to compare
 * @param b second array to compare
 */
function equal(a, b) {
    const aUInt = a instanceof ArrayBuffer ? new Uint8Array(a, 0) : a;
    const bUInt = b instanceof ArrayBuffer ? new Uint8Array(b, 0) : b;
    if (aUInt.byteLength != bUInt.byteLength)
        return false;
    if (aligned32(aUInt) && aligned32(bUInt))
        return compare32(aUInt, bUInt) === 0;
    if (aligned16(aUInt) && aligned16(bUInt))
        return compare16(aUInt, bUInt) === 0;
    return compare8(aUInt, bUInt) === 0;
}
exports.equal = equal;
/**
 * Compares two 8 bit aligned arrays
 * @param a first array to compare
 * @param b second array to compare
 */
function compare8(a, b) {
    const ua = new Uint8Array(a.buffer, a.byteOffset, a.byteLength);
    const ub = new Uint8Array(b.buffer, b.byteOffset, b.byteLength);
    return compare(ua, ub);
}
/**
 * Compares two 16 bit aligned arrays
 * @param a first array to compare
 * @param b second array to compare
 */
function compare16(a, b) {
    const ua = new Uint16Array(a.buffer, a.byteOffset, a.byteLength / 2);
    const ub = new Uint16Array(b.buffer, b.byteOffset, b.byteLength / 2);
    return compare(ua, ub);
}
/**
 * Compares two 32 bit aligned arrays
 * @param a first array to compare
 * @param b second array to compare
 */
function compare32(a, b) {
    const ua = new Uint32Array(a.buffer, a.byteOffset, a.byteLength / 4);
    const ub = new Uint32Array(b.buffer, b.byteOffset, b.byteLength / 4);
    return compare(ua, ub);
}
/**
 * Compare two TypedArrays
 * @param a first array to compare
 * @param b second array to compare
 */
function compare(a, b) {
    if (a.byteLength !== b.byteLength) {
        throw new Error('Cannot compare arrays of different length');
    }
    for (let i = 0; i < a.length - 1; i += 1) {
        if (a[i] > b[i])
            return 1;
        if (a[i] < b[i])
            return -1;
    }
    return 0;
}
exports.compare = compare;
/**
 * Determine if TypedArray is 16 bit aligned
 * @param array The array to check
 */
function aligned16(array) {
    return array.byteOffset % 2 === 0 && array.byteLength % 2 === 0;
}
/**
 * Determine if TypedArray is 32 bit aligned
 * @param array The array to check
 */
function aligned32(array) {
    return array.byteOffset % 4 === 0 && array.byteLength % 4 === 0;
}


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/index.js":
/*!********************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/index.js ***!
  \********************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.decodeSeed = exports.deriveNodeAddress = exports.deriveAddress = exports.verify = exports.sign = exports.deriveKeypair = exports.generateSeed = void 0;
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
Object.defineProperty(exports, "decodeSeed", ({ enumerable: true, get: function () { return ripple_address_codec_1.decodeSeed; } }));
const ripemd160_1 = __webpack_require__(/*! @xrplf/isomorphic/ripemd160 */ "../../node_modules/@xrplf/isomorphic/dist/ripemd160/browser.js");
const sha256_1 = __webpack_require__(/*! @xrplf/isomorphic/sha256 */ "../../node_modules/@xrplf/isomorphic/dist/sha256/browser.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const utils_2 = __webpack_require__(/*! ./signing-schemes/secp256k1/utils */ "../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/utils.js");
const Sha512_1 = __importDefault(__webpack_require__(/*! ./utils/Sha512 */ "../../node_modules/ripple-keypairs/dist/utils/Sha512.js"));
const assert_1 = __importDefault(__webpack_require__(/*! ./utils/assert */ "../../node_modules/ripple-keypairs/dist/utils/assert.js"));
const getAlgorithmFromKey_1 = __webpack_require__(/*! ./utils/getAlgorithmFromKey */ "../../node_modules/ripple-keypairs/dist/utils/getAlgorithmFromKey.js");
const secp256k1_1 = __importDefault(__webpack_require__(/*! ./signing-schemes/secp256k1 */ "../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/index.js"));
const ed25519_1 = __importDefault(__webpack_require__(/*! ./signing-schemes/ed25519 */ "../../node_modules/ripple-keypairs/dist/signing-schemes/ed25519/index.js"));
function getSigningScheme(algorithm) {
    const schemes = { 'ecdsa-secp256k1': secp256k1_1.default, ed25519: ed25519_1.default };
    return schemes[algorithm];
}
function generateSeed(options = {}) {
    assert_1.default.ok(!options.entropy || options.entropy.length >= 16, 'entropy too short');
    const entropy = options.entropy
        ? options.entropy.slice(0, 16)
        : (0, utils_1.randomBytes)(16);
    const type = options.algorithm === 'ed25519' ? 'ed25519' : 'secp256k1';
    return (0, ripple_address_codec_1.encodeSeed)(entropy, type);
}
exports.generateSeed = generateSeed;
function deriveKeypair(seed, options) {
    var _a;
    const decoded = (0, ripple_address_codec_1.decodeSeed)(seed);
    const proposedAlgorithm = (_a = options === null || options === void 0 ? void 0 : options.algorithm) !== null && _a !== void 0 ? _a : decoded.type;
    const algorithm = proposedAlgorithm === 'ed25519' ? 'ed25519' : 'ecdsa-secp256k1';
    const scheme = getSigningScheme(algorithm);
    const keypair = scheme.deriveKeypair(decoded.bytes, options);
    const messageToVerify = Sha512_1.default.half('This test message should verify.');
    const signature = scheme.sign(messageToVerify, keypair.privateKey);
    /* istanbul ignore if */
    if (!scheme.verify(messageToVerify, signature, keypair.publicKey)) {
        throw new Error('derived keypair did not generate verifiable signature');
    }
    return keypair;
}
exports.deriveKeypair = deriveKeypair;
function sign(messageHex, privateKey) {
    const algorithm = (0, getAlgorithmFromKey_1.getAlgorithmFromPrivateKey)(privateKey);
    return getSigningScheme(algorithm).sign((0, utils_1.hexToBytes)(messageHex), privateKey);
}
exports.sign = sign;
function verify(messageHex, signature, publicKey) {
    const algorithm = (0, getAlgorithmFromKey_1.getAlgorithmFromPublicKey)(publicKey);
    return getSigningScheme(algorithm).verify((0, utils_1.hexToBytes)(messageHex), signature, publicKey);
}
exports.verify = verify;
function computePublicKeyHash(publicKeyBytes) {
    return (0, ripemd160_1.ripemd160)((0, sha256_1.sha256)(publicKeyBytes));
}
function deriveAddressFromBytes(publicKeyBytes) {
    return (0, ripple_address_codec_1.encodeAccountID)(computePublicKeyHash(publicKeyBytes));
}
function deriveAddress(publicKey) {
    return deriveAddressFromBytes((0, utils_1.hexToBytes)(publicKey));
}
exports.deriveAddress = deriveAddress;
function deriveNodeAddress(publicKey) {
    const generatorBytes = (0, ripple_address_codec_1.decodeNodePublic)(publicKey);
    const accountPublicBytes = (0, utils_2.accountPublicFromPublicGenerator)(generatorBytes);
    return deriveAddressFromBytes(accountPublicBytes);
}
exports.deriveNodeAddress = deriveNodeAddress;


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/signing-schemes/ed25519/index.js":
/*!********************************************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/signing-schemes/ed25519/index.js ***!
  \********************************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const ed25519_1 = __webpack_require__(/*! @noble/curves/ed25519 */ "../../node_modules/@noble/curves/ed25519.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const assert_1 = __importDefault(__webpack_require__(/*! ../../utils/assert */ "../../node_modules/ripple-keypairs/dist/utils/assert.js"));
const Sha512_1 = __importDefault(__webpack_require__(/*! ../../utils/Sha512 */ "../../node_modules/ripple-keypairs/dist/utils/Sha512.js"));
const ED_PREFIX = 'ED';
const ed25519 = {
    deriveKeypair(entropy) {
        const rawPrivateKey = Sha512_1.default.half(entropy);
        const privateKey = ED_PREFIX + (0, utils_1.bytesToHex)(rawPrivateKey);
        const publicKey = ED_PREFIX + (0, utils_1.bytesToHex)(ed25519_1.ed25519.getPublicKey(rawPrivateKey));
        return { privateKey, publicKey };
    },
    sign(message, privateKey) {
        assert_1.default.ok(message instanceof Uint8Array, 'message must be array of octets');
        assert_1.default.ok(privateKey.length === 66, 'private key must be 33 bytes including prefix');
        return (0, utils_1.bytesToHex)(ed25519_1.ed25519.sign(message, privateKey.slice(2)));
    },
    verify(message, signature, publicKey) {
        // Unlikely to be triggered as these are internal and guarded by getAlgorithmFromKey
        assert_1.default.ok(publicKey.length === 66, 'public key must be 33 bytes including prefix');
        return ed25519_1.ed25519.verify(signature, message, 
        // Remove the 0xED prefix
        publicKey.slice(2), 
        // By default, set zip215 to false for compatibility reasons.
        // ZIP 215 is a stricter Ed25519 signature verification scheme.
        // However, setting it to false adheres to the more commonly used
        // RFC8032 / NIST186-5 standards, making it compatible with systems
        // like the XRP Ledger.
        { zip215: false });
    },
};
exports["default"] = ed25519;


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/index.js":
/*!**********************************************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/index.js ***!
  \**********************************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const utils_1 = __webpack_require__(/*! @noble/curves/abstract/utils */ "../../node_modules/@noble/curves/abstract/utils.js");
const secp256k1_1 = __webpack_require__(/*! @noble/curves/secp256k1 */ "../../node_modules/@noble/curves/secp256k1.js");
const utils_2 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const utils_3 = __webpack_require__(/*! ./utils */ "../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/utils.js");
const assert_1 = __importDefault(__webpack_require__(/*! ../../utils/assert */ "../../node_modules/ripple-keypairs/dist/utils/assert.js"));
const Sha512_1 = __importDefault(__webpack_require__(/*! ../../utils/Sha512 */ "../../node_modules/ripple-keypairs/dist/utils/Sha512.js"));
const SECP256K1_PREFIX = '00';
const secp256k1 = {
    deriveKeypair(entropy, options) {
        const derived = (0, utils_3.derivePrivateKey)(entropy, options);
        const privateKey = SECP256K1_PREFIX + (0, utils_2.bytesToHex)((0, utils_1.numberToBytesBE)(derived, 32));
        const publicKey = (0, utils_2.bytesToHex)(secp256k1_1.secp256k1.getPublicKey(derived, true));
        return { privateKey, publicKey };
    },
    sign(message, privateKey) {
        // Some callers pass the privateKey with the prefix, others without.
        // @noble/curves will throw if the key is not exactly 32 bytes, so we
        // normalize it before passing to the sign method.
        assert_1.default.ok((privateKey.length === 66 && privateKey.startsWith(SECP256K1_PREFIX)) ||
            privateKey.length === 64);
        const normedPrivateKey = privateKey.length === 66 ? privateKey.slice(2) : privateKey;
        return secp256k1_1.secp256k1
            .sign(Sha512_1.default.half(message), normedPrivateKey, {
            // "Canonical" signatures
            lowS: true,
            // Would fail tests if signatures aren't deterministic
            extraEntropy: undefined,
        })
            .toDERHex(true)
            .toUpperCase();
    },
    verify(message, signature, publicKey) {
        const decoded = secp256k1_1.secp256k1.Signature.fromDER(signature);
        return secp256k1_1.secp256k1.verify(decoded, Sha512_1.default.half(message), publicKey);
    },
};
exports["default"] = secp256k1;


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/utils.js":
/*!**********************************************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/signing-schemes/secp256k1/utils.js ***!
  \**********************************************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.accountPublicFromPublicGenerator = exports.derivePrivateKey = void 0;
const secp256k1_1 = __webpack_require__(/*! @noble/curves/secp256k1 */ "../../node_modules/@noble/curves/secp256k1.js");
const Sha512_1 = __importDefault(__webpack_require__(/*! ../../utils/Sha512 */ "../../node_modules/ripple-keypairs/dist/utils/Sha512.js"));
const ZERO = BigInt(0);
function deriveScalar(bytes, discrim) {
    const order = secp256k1_1.secp256k1.CURVE.n;
    for (let i = 0; i <= 4294967295; i++) {
        // We hash the bytes to find a 256-bit number, looping until we are sure it
        // is less than the order of the curve.
        const hasher = new Sha512_1.default().add(bytes);
        // If the optional discriminator index was passed in, update the hash.
        if (discrim !== undefined) {
            hasher.addU32(discrim);
        }
        hasher.addU32(i);
        const key = hasher.first256BigInt();
        /* istanbul ignore else */
        if (key > ZERO && key < order) {
            return key;
        }
    }
    // This error is practically impossible to reach.
    // The order of the curve describes the (finite) amount of points on the curve
    // https://github.com/indutny/elliptic/blob/master/lib/elliptic/curves.js#L182
    // How often will an (essentially) random number generated by Sha512 be larger than that?
    // There's 2^32 chances (the for loop) to get a number smaller than the order,
    // and it's rare that you'll even get past the first loop iteration.
    // Note that in TypeScript we actually need the throw, otherwise the function signature would be bigint | undefined
    //
    /* istanbul ignore next */
    throw new Error('impossible unicorn ;)');
}
/**
 * @param seed - Bytes.
 * @param [opts] - Object.
 * @param [opts.accountIndex=0] - The account number to generate.
 * @param [opts.validator=false] - Generate root key-pair,
 *                                              as used by validators.
 * @returns {bigint} 256 bit scalar value.
 *
 */
function derivePrivateKey(seed, opts = {}) {
    const root = opts.validator;
    const order = secp256k1_1.secp256k1.CURVE.n;
    // This private generator represents the `root` private key, and is what's
    // used by validators for signing when a keypair is generated from a seed.
    const privateGen = deriveScalar(seed);
    if (root) {
        // As returned by validation_create for a given seed
        return privateGen;
    }
    const publicGen = secp256k1_1.secp256k1.ProjectivePoint.BASE.multiply(privateGen).toRawBytes(true);
    // A seed can generate many keypairs as a function of the seed and a uint32.
    // Almost everyone just uses the first account, `0`.
    const accountIndex = opts.accountIndex || 0;
    return (deriveScalar(publicGen, accountIndex) + privateGen) % order;
}
exports.derivePrivateKey = derivePrivateKey;
function accountPublicFromPublicGenerator(publicGenBytes) {
    const rootPubPoint = secp256k1_1.secp256k1.ProjectivePoint.fromHex(publicGenBytes);
    const scalar = deriveScalar(publicGenBytes, 0);
    const point = secp256k1_1.secp256k1.ProjectivePoint.BASE.multiply(scalar);
    const offset = rootPubPoint.add(point);
    return offset.toRawBytes(true);
}
exports.accountPublicFromPublicGenerator = accountPublicFromPublicGenerator;


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/utils/Sha512.js":
/*!***************************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/utils/Sha512.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const sha512_1 = __webpack_require__(/*! @xrplf/isomorphic/sha512 */ "../../node_modules/@xrplf/isomorphic/dist/sha512/browser.js");
const utils_1 = __webpack_require__(/*! @noble/curves/abstract/utils */ "../../node_modules/@noble/curves/abstract/utils.js");
class Sha512 {
    constructor() {
        // instantiate empty sha512 hash
        this.hash = sha512_1.sha512.create();
    }
    static half(input) {
        return new Sha512().add(input).first256();
    }
    add(bytes) {
        this.hash.update(bytes);
        return this;
    }
    addU32(i) {
        const buffer = new Uint8Array(4);
        new DataView(buffer.buffer).setUint32(0, i);
        return this.add(buffer);
    }
    finish() {
        return this.hash.digest();
    }
    first256() {
        return this.finish().slice(0, 32);
    }
    first256BigInt() {
        return (0, utils_1.bytesToNumberBE)(this.first256());
    }
}
exports["default"] = Sha512;


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/utils/assert.js":
/*!***************************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/utils/assert.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const assertHelper = {
    ok(cond, message) {
        if (!cond) {
            throw new Error(message);
        }
    },
};
exports["default"] = assertHelper;


/***/ }),

/***/ "../../node_modules/ripple-keypairs/dist/utils/getAlgorithmFromKey.js":
/*!****************************************************************************!*\
  !*** ../../node_modules/ripple-keypairs/dist/utils/getAlgorithmFromKey.js ***!
  \****************************************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.getAlgorithmFromPrivateKey = exports.getAlgorithmFromPublicKey = exports.getAlgorithmFromKey = void 0;
var Prefix;
(function (Prefix) {
    Prefix[Prefix["NONE"] = -1] = "NONE";
    Prefix[Prefix["ED25519"] = 237] = "ED25519";
    Prefix[Prefix["SECP256K1_PUB_X"] = 2] = "SECP256K1_PUB_X";
    Prefix[Prefix["SECP256K1_PUB_X_ODD_Y"] = 3] = "SECP256K1_PUB_X_ODD_Y";
    Prefix[Prefix["SECP256K1_PUB_XY"] = 4] = "SECP256K1_PUB_XY";
    Prefix[Prefix["SECP256K1_PRIVATE"] = 0] = "SECP256K1_PRIVATE";
})(Prefix || (Prefix = {}));
/**
 * | Curve     | Type        | Prefix | Length | Description                                           | Algorithm       |
 * |-----------|-------------|:------:|:------:|-------------------------------------------------------|----------------:|
 * | ed25519   | Private     |  0xED  |   33   | prefix + Uint256LE (0 < n < order )                   |         ed25519 |
 * | ed25519   | Public      |  0xED  |   33   | prefix + 32 y-bytes                                   |         ed25519 |
 * | secp256k1 | Public (1)  |  0x02  |   33   | prefix + 32 x-bytes                                   | ecdsa-secp256k1 |
 * | secp256k1 | Public (2)  |  0x03  |   33   | prefix + 32 x-bytes (y is odd)                        | ecdsa-secp256k1 |
 * | secp256k1 | Public (3)  |  0x04  |   65   | prefix + 32 x-bytes + 32 y-bytes                      | ecdsa-secp256k1 |
 * | secp256k1 | Private (1) |  None  |   32   | Uint256BE (0 < n < order)                             | ecdsa-secp256k1 |
 * | secp256k1 | Private (2) |  0x00  |   33   | prefix + Uint256BE (0 < n < order)                    | ecdsa-secp256k1 |
 *
 * Note: The 0x00 prefix for secpk256k1 Private (2) essentially 0 pads the number
 *       and the interpreted number is the same as 32 bytes.
 */
const KEY_TYPES = {
    [`private_${Prefix.NONE}_32`]: 'ecdsa-secp256k1',
    [`private_${Prefix.SECP256K1_PRIVATE}_33`]: 'ecdsa-secp256k1',
    [`private_${Prefix.ED25519}_33`]: 'ed25519',
    [`public_${Prefix.ED25519}_33`]: 'ed25519',
    [`public_${Prefix.SECP256K1_PUB_X}_33`]: 'ecdsa-secp256k1',
    [`public_${Prefix.SECP256K1_PUB_X_ODD_Y}_33`]: 'ecdsa-secp256k1',
    [`public_${Prefix.SECP256K1_PUB_XY}_65`]: 'ecdsa-secp256k1',
};
function getKeyInfo(key) {
    return {
        prefix: key.length < 2 ? Prefix.NONE : parseInt(key.slice(0, 2), 16),
        len: key.length / 2,
    };
}
function prefixRepr(prefix) {
    return prefix === Prefix.NONE
        ? 'None'
        : `0x${prefix.toString(16).padStart(2, '0')}`;
}
function getValidFormatsTable(type) {
    // No need overkill with renderTable method
    const padding = 2;
    const colWidth = {
        algorithm: 'ecdsa-secp256k1'.length + padding,
        prefix: '0x00'.length + padding,
    };
    return Object.entries(KEY_TYPES)
        .filter(([key]) => key.startsWith(type))
        .map(([key, algorithm]) => {
        const [, prefix, length] = key.split('_');
        const paddedAlgo = algorithm.padEnd(colWidth.algorithm);
        const paddedPrefix = prefixRepr(Number(prefix)).padEnd(colWidth.prefix);
        return `${paddedAlgo} - Prefix: ${paddedPrefix} Length: ${length} bytes`;
    })
        .join('\n');
}
function keyError({ key, type, prefix, len, }) {
    const validFormats = getValidFormatsTable(type);
    return `invalid_key:

Type: ${type}
Key: ${key}
Prefix: ${prefixRepr(prefix)} 
Length: ${len} bytes

Acceptable ${type} formats are:
${validFormats}
`;
}
/**
 * Determines the algorithm associated with a given key (public/private).
 *
 * @param key - hexadecimal string representation of the key.
 * @param type - whether expected key is public or private
 * @returns Algorithm algorithm for signing/verifying
 * @throws Error when key is invalid
 */
function getAlgorithmFromKey(key, type) {
    const { prefix, len } = getKeyInfo(key);
    // Special case back compat support for no prefix
    const usedPrefix = type === 'private' && len === 32 ? Prefix.NONE : prefix;
    const algorithm = KEY_TYPES[`${type}_${usedPrefix}_${len}`];
    if (!algorithm) {
        throw new Error(keyError({ key, type, len, prefix: usedPrefix }));
    }
    return algorithm;
}
exports.getAlgorithmFromKey = getAlgorithmFromKey;
function getAlgorithmFromPublicKey(key) {
    return getAlgorithmFromKey(key, 'public');
}
exports.getAlgorithmFromPublicKey = getAlgorithmFromPublicKey;
function getAlgorithmFromPrivateKey(key) {
    return getAlgorithmFromKey(key, 'private');
}
exports.getAlgorithmFromPrivateKey = getAlgorithmFromPrivateKey;


/***/ }),

/***/ "./dist/npm/ECDSA.js":
/*!***************************!*\
  !*** ./dist/npm/ECDSA.js ***!
  \***************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
var ECDSA;
(function (ECDSA) {
    ECDSA["ed25519"] = "ed25519";
    ECDSA["secp256k1"] = "ecdsa-secp256k1";
})(ECDSA || (ECDSA = {}));
exports["default"] = ECDSA;


/***/ }),

/***/ "./dist/npm/Wallet/defaultFaucets.js":
/*!*******************************************!*\
  !*** ./dist/npm/Wallet/defaultFaucets.js ***!
  \*******************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.getDefaultFaucetPath = exports.getFaucetHost = exports.FaucetNetworkPaths = exports.FaucetNetwork = void 0;
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
var FaucetNetwork;
(function (FaucetNetwork) {
    FaucetNetwork["Testnet"] = "faucet.altnet.rippletest.net";
    FaucetNetwork["Devnet"] = "faucet.devnet.rippletest.net";
})(FaucetNetwork || (exports.FaucetNetwork = FaucetNetwork = {}));
exports.FaucetNetworkPaths = {
    [FaucetNetwork.Testnet]: '/accounts',
    [FaucetNetwork.Devnet]: '/accounts',
};
function getFaucetHost(client) {
    const connectionUrl = client.url;
    if (connectionUrl.includes('altnet') || connectionUrl.includes('testnet')) {
        return FaucetNetwork.Testnet;
    }
    if (connectionUrl.includes('sidechain-net2')) {
        throw new errors_1.XRPLFaucetError('Cannot fund an account on an issuing chain. Accounts must be created via the bridge.');
    }
    if (connectionUrl.includes('devnet')) {
        return FaucetNetwork.Devnet;
    }
    throw new errors_1.XRPLFaucetError('Faucet URL is not defined or inferrable.');
}
exports.getFaucetHost = getFaucetHost;
function getDefaultFaucetPath(hostname) {
    if (hostname === undefined) {
        return '/accounts';
    }
    return exports.FaucetNetworkPaths[hostname] || '/accounts';
}
exports.getDefaultFaucetPath = getDefaultFaucetPath;


/***/ }),

/***/ "./dist/npm/Wallet/fundWallet.js":
/*!***************************************!*\
  !*** ./dist/npm/Wallet/fundWallet.js ***!
  \***************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.requestFunding = exports.getStartingBalance = exports.generateWalletToFund = void 0;
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const defaultFaucets_1 = __webpack_require__(/*! ./defaultFaucets */ "./dist/npm/Wallet/defaultFaucets.js");
const _1 = __webpack_require__(/*! . */ "./dist/npm/Wallet/index.js");
const INTERVAL_SECONDS = 1;
const MAX_ATTEMPTS = 20;
function generateWalletToFund(wallet) {
    if (wallet && (0, ripple_address_codec_1.isValidClassicAddress)(wallet.classicAddress)) {
        return wallet;
    }
    return _1.Wallet.generate();
}
exports.generateWalletToFund = generateWalletToFund;
function getStartingBalance(client, classicAddress) {
    return __awaiter(this, void 0, void 0, function* () {
        let startingBalance = 0;
        try {
            startingBalance = Number(yield client.getXrpBalance(classicAddress));
        }
        catch (_a) {
        }
        return startingBalance;
    });
}
exports.getStartingBalance = getStartingBalance;
function requestFunding(options, client, startingBalance, walletToFund, postBody) {
    var _a, _b, _c;
    return __awaiter(this, void 0, void 0, function* () {
        const hostname = (_a = options.faucetHost) !== null && _a !== void 0 ? _a : (0, defaultFaucets_1.getFaucetHost)(client);
        if (!hostname) {
            throw new errors_1.XRPLFaucetError('No faucet hostname could be derived');
        }
        const pathname = (_b = options.faucetPath) !== null && _b !== void 0 ? _b : (0, defaultFaucets_1.getDefaultFaucetPath)(hostname);
        const response = yield fetch(`https://${hostname}${pathname}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(postBody),
        });
        const body = yield response.json();
        if (response.ok &&
            ((_c = response.headers.get('Content-Type')) === null || _c === void 0 ? void 0 : _c.startsWith('application/json'))) {
            const classicAddress = body.account.classicAddress;
            return processSuccessfulResponse(client, classicAddress, walletToFund, startingBalance);
        }
        return processError(response, body);
    });
}
exports.requestFunding = requestFunding;
function processSuccessfulResponse(client, classicAddress, walletToFund, startingBalance) {
    return __awaiter(this, void 0, void 0, function* () {
        if (!classicAddress) {
            return Promise.reject(new errors_1.XRPLFaucetError(`The faucet account is undefined`));
        }
        try {
            const updatedBalance = yield getUpdatedBalance(client, classicAddress, startingBalance);
            if (updatedBalance > startingBalance) {
                return {
                    wallet: walletToFund,
                    balance: updatedBalance,
                };
            }
            throw new errors_1.XRPLFaucetError(`Unable to fund address with faucet after waiting ${INTERVAL_SECONDS * MAX_ATTEMPTS} seconds`);
        }
        catch (err) {
            if (err instanceof Error) {
                throw new errors_1.XRPLFaucetError(err.message);
            }
            throw err;
        }
    });
}
function processError(response, body) {
    return __awaiter(this, void 0, void 0, function* () {
        return Promise.reject(new errors_1.XRPLFaucetError(`Request failed: ${JSON.stringify({
            body: body || {},
            contentType: response.headers.get('Content-Type'),
            statusCode: response.status,
        })}`));
    });
}
function getUpdatedBalance(client, address, originalBalance) {
    return __awaiter(this, void 0, void 0, function* () {
        return new Promise((resolve, reject) => {
            let attempts = MAX_ATTEMPTS;
            const interval = setInterval(() => __awaiter(this, void 0, void 0, function* () {
                if (attempts < 0) {
                    clearInterval(interval);
                    resolve(originalBalance);
                }
                else {
                    attempts -= 1;
                }
                try {
                    let newBalance;
                    try {
                        newBalance = Number(yield client.getXrpBalance(address));
                    }
                    catch (_a) {
                    }
                    if (newBalance > originalBalance) {
                        clearInterval(interval);
                        resolve(newBalance);
                    }
                }
                catch (err) {
                    clearInterval(interval);
                    if (err instanceof Error) {
                        reject(new errors_1.XRPLFaucetError(`Unable to check if the address ${address} balance has increased. Error: ${err.message}`));
                    }
                    reject(err);
                }
            }), INTERVAL_SECONDS * 1000);
        });
    });
}


/***/ }),

/***/ "./dist/npm/Wallet/index.js":
/*!**********************************!*\
  !*** ./dist/npm/Wallet/index.js ***!
  \**********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Wallet = void 0;
const bip32_1 = __webpack_require__(/*! @scure/bip32 */ "../../node_modules/@scure/bip32/lib/index.js");
const bip39_1 = __webpack_require__(/*! @scure/bip39 */ "../../node_modules/@scure/bip39/index.js");
const english_1 = __webpack_require__(/*! @scure/bip39/wordlists/english */ "../../node_modules/@scure/bip39/wordlists/english.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
const ECDSA_1 = __importDefault(__webpack_require__(/*! ../ECDSA */ "./dist/npm/ECDSA.js"));
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const transactions_1 = __webpack_require__(/*! ../models/transactions */ "./dist/npm/models/transactions/index.js");
const utils_2 = __webpack_require__(/*! ../sugar/utils */ "./dist/npm/sugar/utils.js");
const collections_1 = __webpack_require__(/*! ../utils/collections */ "./dist/npm/utils/collections.js");
const hashLedger_1 = __webpack_require__(/*! ../utils/hashes/hashLedger */ "./dist/npm/utils/hashes/hashLedger.js");
const rfc1751_1 = __webpack_require__(/*! ./rfc1751 */ "./dist/npm/Wallet/rfc1751.js");
const signer_1 = __webpack_require__(/*! ./signer */ "./dist/npm/Wallet/signer.js");
const DEFAULT_ALGORITHM = ECDSA_1.default.ed25519;
const DEFAULT_DERIVATION_PATH = "m/44'/144'/0'/0/0";
function validateKey(node) {
    if (!(node.privateKey instanceof Uint8Array)) {
        throw new errors_1.ValidationError('Unable to derive privateKey from mnemonic input');
    }
    if (!(node.publicKey instanceof Uint8Array)) {
        throw new errors_1.ValidationError('Unable to derive publicKey from mnemonic input');
    }
}
class Wallet {
    constructor(publicKey, privateKey, opts = {}) {
        this.publicKey = publicKey;
        this.privateKey = privateKey;
        this.classicAddress = opts.masterAddress
            ? (0, utils_2.ensureClassicAddress)(opts.masterAddress)
            : (0, ripple_keypairs_1.deriveAddress)(publicKey);
        this.seed = opts.seed;
    }
    get address() {
        return this.classicAddress;
    }
    static generate(algorithm = DEFAULT_ALGORITHM) {
        if (!Object.values(ECDSA_1.default).includes(algorithm)) {
            throw new errors_1.ValidationError('Invalid cryptographic signing algorithm');
        }
        const seed = (0, ripple_keypairs_1.generateSeed)({ algorithm });
        return Wallet.fromSeed(seed, { algorithm });
    }
    static fromSeed(seed, opts = {}) {
        return Wallet.deriveWallet(seed, {
            algorithm: opts.algorithm,
            masterAddress: opts.masterAddress,
        });
    }
    static fromEntropy(entropy, opts = {}) {
        var _a;
        const algorithm = (_a = opts.algorithm) !== null && _a !== void 0 ? _a : DEFAULT_ALGORITHM;
        const options = {
            entropy: Uint8Array.from(entropy),
            algorithm,
        };
        const seed = (0, ripple_keypairs_1.generateSeed)(options);
        return Wallet.deriveWallet(seed, {
            algorithm,
            masterAddress: opts.masterAddress,
        });
    }
    static fromMnemonic(mnemonic, opts = {}) {
        var _a;
        if (opts.mnemonicEncoding === 'rfc1751') {
            return Wallet.fromRFC1751Mnemonic(mnemonic, {
                masterAddress: opts.masterAddress,
                algorithm: opts.algorithm,
            });
        }
        if (!(0, bip39_1.validateMnemonic)(mnemonic, english_1.wordlist)) {
            throw new errors_1.ValidationError('Unable to parse the given mnemonic using bip39 encoding');
        }
        const seed = (0, bip39_1.mnemonicToSeedSync)(mnemonic);
        const masterNode = bip32_1.HDKey.fromMasterSeed(seed);
        const node = masterNode.derive((_a = opts.derivationPath) !== null && _a !== void 0 ? _a : DEFAULT_DERIVATION_PATH);
        validateKey(node);
        const publicKey = (0, utils_1.bytesToHex)(node.publicKey);
        const privateKey = (0, utils_1.bytesToHex)(node.privateKey);
        return new Wallet(publicKey, `00${privateKey}`, {
            masterAddress: opts.masterAddress,
        });
    }
    static fromRFC1751Mnemonic(mnemonic, opts) {
        const seed = (0, rfc1751_1.rfc1751MnemonicToKey)(mnemonic);
        let encodeAlgorithm;
        if (opts.algorithm === ECDSA_1.default.ed25519) {
            encodeAlgorithm = 'ed25519';
        }
        else {
            encodeAlgorithm = 'secp256k1';
        }
        const encodedSeed = (0, ripple_address_codec_1.encodeSeed)(seed, encodeAlgorithm);
        return Wallet.fromSeed(encodedSeed, {
            masterAddress: opts.masterAddress,
            algorithm: opts.algorithm,
        });
    }
    static deriveWallet(seed, opts = {}) {
        var _a;
        const { publicKey, privateKey } = (0, ripple_keypairs_1.deriveKeypair)(seed, {
            algorithm: (_a = opts.algorithm) !== null && _a !== void 0 ? _a : DEFAULT_ALGORITHM,
        });
        return new Wallet(publicKey, privateKey, {
            seed,
            masterAddress: opts.masterAddress,
        });
    }
    sign(transaction, multisign) {
        let multisignAddress = false;
        if (typeof multisign === 'string' && multisign.startsWith('X')) {
            multisignAddress = multisign;
        }
        else if (multisign) {
            multisignAddress = this.classicAddress;
        }
        const tx = (0, collections_1.omitBy)(Object.assign({}, transaction), (value) => value == null);
        if (tx.TxnSignature || tx.Signers) {
            throw new errors_1.ValidationError('txJSON must not contain "TxnSignature" or "Signers" properties');
        }
        removeTrailingZeros(tx);
        (0, transactions_1.validate)(tx);
        const txToSignAndEncode = Object.assign({}, tx);
        txToSignAndEncode.SigningPubKey = multisignAddress ? '' : this.publicKey;
        if (multisignAddress) {
            const signer = {
                Account: multisignAddress,
                SigningPubKey: this.publicKey,
                TxnSignature: computeSignature(txToSignAndEncode, this.privateKey, multisignAddress),
            };
            txToSignAndEncode.Signers = [{ Signer: signer }];
        }
        else {
            txToSignAndEncode.TxnSignature = computeSignature(txToSignAndEncode, this.privateKey);
        }
        const serialized = (0, ripple_binary_codec_1.encode)(txToSignAndEncode);
        return {
            tx_blob: serialized,
            hash: (0, hashLedger_1.hashSignedTx)(serialized),
        };
    }
    verifyTransaction(signedTransaction) {
        return (0, signer_1.verifySignature)(signedTransaction, this.publicKey);
    }
    getXAddress(tag = false, isTestnet = false) {
        return (0, ripple_address_codec_1.classicAddressToXAddress)(this.classicAddress, tag, isTestnet);
    }
}
exports.Wallet = Wallet;
Wallet.fromSecret = Wallet.fromSeed;
function computeSignature(tx, privateKey, signAs) {
    if (signAs) {
        const classicAddress = (0, ripple_address_codec_1.isValidXAddress)(signAs)
            ? (0, ripple_address_codec_1.xAddressToClassicAddress)(signAs).classicAddress
            : signAs;
        return (0, ripple_keypairs_1.sign)((0, ripple_binary_codec_1.encodeForMultisigning)(tx, classicAddress), privateKey);
    }
    return (0, ripple_keypairs_1.sign)((0, ripple_binary_codec_1.encodeForSigning)(tx), privateKey);
}
function removeTrailingZeros(tx) {
    if (tx.TransactionType === 'Payment' &&
        typeof tx.Amount !== 'string' &&
        tx.Amount.value.includes('.') &&
        tx.Amount.value.endsWith('0')) {
        tx.Amount = Object.assign({}, tx.Amount);
        tx.Amount.value = new bignumber_js_1.default(tx.Amount.value).toString();
    }
}


/***/ }),

/***/ "./dist/npm/Wallet/rfc1751.js":
/*!************************************!*\
  !*** ./dist/npm/Wallet/rfc1751.js ***!
  \************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.keyToRFC1751Mnemonic = exports.rfc1751MnemonicToKey = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const rfc1751Words_json_1 = __importDefault(__webpack_require__(/*! ./rfc1751Words.json */ "./dist/npm/Wallet/rfc1751Words.json"));
const rfc1751WordList = rfc1751Words_json_1.default;
const BINARY = ['0000', '0001', '0010', '0011', '0100', '0101', '0110', '0111',
    '1000', '1001', '1010', '1011', '1100', '1101', '1110', '1111'];
function keyToBinary(key) {
    let res = '';
    for (const num of key) {
        res += BINARY[num >> 4] + BINARY[num & 0x0f];
    }
    return res;
}
function extract(key, start, length) {
    const subKey = key.substring(start, start + length);
    let acc = 0;
    for (let index = 0; index < subKey.length; index++) {
        acc = acc * 2 + subKey.charCodeAt(index) - 48;
    }
    return acc;
}
function keyToRFC1751Mnemonic(hex_key) {
    const buf = (0, utils_1.hexToBytes)(hex_key.replace(/\s+/gu, ''));
    let key = bufferToArray(swap128(buf));
    const padding = [];
    for (let index = 0; index < (8 - (key.length % 8)) % 8; index++) {
        padding.push(0);
    }
    key = padding.concat(key);
    const english = [];
    for (let index = 0; index < key.length; index += 8) {
        const subKey = key.slice(index, index + 8);
        let skbin = keyToBinary(subKey);
        let parity = 0;
        for (let j = 0; j < 64; j += 2) {
            parity += extract(skbin, j, 2);
        }
        subKey.push((parity << 6) & 0xff);
        skbin = keyToBinary(subKey);
        for (let j = 0; j < 64; j += 11) {
            english.push(rfc1751WordList[extract(skbin, j, 11)]);
        }
    }
    return english.join(' ');
}
exports.keyToRFC1751Mnemonic = keyToRFC1751Mnemonic;
function rfc1751MnemonicToKey(english) {
    const words = english.split(' ');
    let key = [];
    for (let index = 0; index < words.length; index += 6) {
        const { subKey, word } = getSubKey(words, index);
        const skbin = keyToBinary(subKey);
        let parity = 0;
        for (let j = 0; j < 64; j += 2) {
            parity += extract(skbin, j, 2);
        }
        const cs0 = extract(skbin, 64, 2);
        const cs1 = parity & 3;
        if (cs0 !== cs1) {
            throw new Error(`Parity error at ${word}`);
        }
        key = key.concat(subKey.slice(0, 8));
    }
    const bufferKey = swap128(Uint8Array.from(key));
    return bufferKey;
}
exports.rfc1751MnemonicToKey = rfc1751MnemonicToKey;
function getSubKey(words, index) {
    const sublist = words.slice(index, index + 6);
    let bits = 0;
    const ch = [0, 0, 0, 0, 0, 0, 0, 0, 0];
    let word = '';
    for (word of sublist) {
        const idx = rfc1751WordList.indexOf(word.toUpperCase());
        if (idx === -1) {
            throw new TypeError(`Expected an RFC1751 word, but received '${word}'. ` +
                `For the full list of words in the RFC1751 encoding see https://datatracker.ietf.org/doc/html/rfc1751`);
        }
        const shift = (8 - ((bits + 11) % 8)) % 8;
        const y = idx << shift;
        const cl = y >> 16;
        const cc = (y >> 8) & 0xff;
        const cr = y & 0xff;
        const t = Math.floor(bits / 8);
        if (shift > 5) {
            ch[t] |= cl;
            ch[t + 1] |= cc;
            ch[t + 2] |= cr;
        }
        else if (shift > -3) {
            ch[t] |= cc;
            ch[t + 1] |= cr;
        }
        else {
            ch[t] |= cr;
        }
        bits += 11;
    }
    const subKey = ch.slice();
    return { subKey, word };
}
function bufferToArray(buf) {
    return Array.prototype.slice.call(buf);
}
function swap(arr, n, m) {
    const i = arr[n];
    arr[n] = arr[m];
    arr[m] = i;
}
function swap64(arr) {
    const len = arr.length;
    for (let i = 0; i < len; i += 8) {
        swap(arr, i, i + 7);
        swap(arr, i + 1, i + 6);
        swap(arr, i + 2, i + 5);
        swap(arr, i + 3, i + 4);
    }
    return arr;
}
function swap128(arr) {
    const reversedBytes = swap64(arr);
    return (0, utils_1.concat)([reversedBytes.slice(8, 16), reversedBytes.slice(0, 8)]);
}


/***/ }),

/***/ "./dist/npm/Wallet/signer.js":
/*!***********************************!*\
  !*** ./dist/npm/Wallet/signer.js ***!
  \***********************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.multisign = exports.verifySignature = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const bignumber_js_1 = __webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js");
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const transactions_1 = __webpack_require__(/*! ../models/transactions */ "./dist/npm/models/transactions/index.js");
function multisign(transactions) {
    if (transactions.length === 0) {
        throw new errors_1.ValidationError('There were 0 transactions to multisign');
    }
    const decodedTransactions = transactions.map((txOrBlob) => {
        return getDecodedTransaction(txOrBlob);
    });
    decodedTransactions.forEach((tx) => {
        (0, transactions_1.validate)(tx);
        if (tx.Signers == null || tx.Signers.length === 0) {
            throw new errors_1.ValidationError("For multisigning all transactions must include a Signers field containing an array of signatures. You may have forgotten to pass the 'forMultisign' parameter when signing.");
        }
        if (tx.SigningPubKey !== '') {
            throw new errors_1.ValidationError('SigningPubKey must be an empty string for all transactions when multisigning.');
        }
    });
    validateTransactionEquivalence(decodedTransactions);
    return (0, ripple_binary_codec_1.encode)(getTransactionWithAllSigners(decodedTransactions));
}
exports.multisign = multisign;
function verifySignature(tx, publicKey) {
    const decodedTx = getDecodedTransaction(tx);
    let key = publicKey;
    if (typeof decodedTx.TxnSignature !== 'string' || !decodedTx.TxnSignature) {
        throw new Error('Transaction is missing a signature, TxnSignature');
    }
    if (!key) {
        if (typeof decodedTx.SigningPubKey !== 'string' ||
            !decodedTx.SigningPubKey) {
            throw new Error('Transaction is missing a public key, SigningPubKey');
        }
        key = decodedTx.SigningPubKey;
    }
    return (0, ripple_keypairs_1.verify)((0, ripple_binary_codec_1.encodeForSigning)(decodedTx), decodedTx.TxnSignature, key);
}
exports.verifySignature = verifySignature;
function validateTransactionEquivalence(transactions) {
    const exampleTransaction = JSON.stringify(Object.assign(Object.assign({}, transactions[0]), { Signers: null }));
    if (transactions
        .slice(1)
        .some((tx) => JSON.stringify(Object.assign(Object.assign({}, tx), { Signers: null })) !== exampleTransaction)) {
        throw new errors_1.ValidationError('txJSON is not the same for all signedTransactions');
    }
}
function getTransactionWithAllSigners(transactions) {
    const sortedSigners = transactions
        .flatMap((tx) => { var _a; return (_a = tx.Signers) !== null && _a !== void 0 ? _a : []; })
        .sort(compareSigners);
    return Object.assign(Object.assign({}, transactions[0]), { Signers: sortedSigners });
}
function compareSigners(left, right) {
    return addressToBigNumber(left.Signer.Account).comparedTo(addressToBigNumber(right.Signer.Account));
}
const NUM_BITS_IN_HEX = 16;
function addressToBigNumber(address) {
    const hex = (0, utils_1.bytesToHex)((0, ripple_address_codec_1.decodeAccountID)(address));
    return new bignumber_js_1.BigNumber(hex, NUM_BITS_IN_HEX);
}
function getDecodedTransaction(txOrBlob) {
    if (typeof txOrBlob === 'object') {
        return (0, ripple_binary_codec_1.decode)((0, ripple_binary_codec_1.encode)(txOrBlob));
    }
    return (0, ripple_binary_codec_1.decode)(txOrBlob);
}


/***/ }),

/***/ "./dist/npm/Wallet/walletFromSecretNumbers.js":
/*!****************************************************!*\
  !*** ./dist/npm/Wallet/walletFromSecretNumbers.js ***!
  \****************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.walletFromSecretNumbers = void 0;
const secret_numbers_1 = __webpack_require__(/*! @xrplf/secret-numbers */ "../../node_modules/@xrplf/secret-numbers/dist/index.js");
const ECDSA_1 = __importDefault(__webpack_require__(/*! ../ECDSA */ "./dist/npm/ECDSA.js"));
const _1 = __webpack_require__(/*! . */ "./dist/npm/Wallet/index.js");
function walletFromSecretNumbers(secretNumbers, opts) {
    var _a;
    const secret = new secret_numbers_1.Account(secretNumbers).getFamilySeed();
    const updatedOpts = {
        masterAddress: undefined,
        algorithm: undefined,
    };
    if (opts === undefined) {
        updatedOpts.algorithm = ECDSA_1.default.secp256k1;
    }
    else {
        updatedOpts.masterAddress = opts.masterAddress;
        updatedOpts.algorithm = (_a = opts.algorithm) !== null && _a !== void 0 ? _a : ECDSA_1.default.secp256k1;
    }
    return _1.Wallet.fromSecret(secret, updatedOpts);
}
exports.walletFromSecretNumbers = walletFromSecretNumbers;


/***/ }),

/***/ "./dist/npm/client/ConnectionManager.js":
/*!**********************************************!*\
  !*** ./dist/npm/client/ConnectionManager.js ***!
  \**********************************************/
/***/ (function(__unused_webpack_module, exports) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
class ConnectionManager {
    constructor() {
        this.promisesAwaitingConnection = [];
    }
    resolveAllAwaiting() {
        this.promisesAwaitingConnection.map(({ resolve }) => resolve());
        this.promisesAwaitingConnection = [];
    }
    rejectAllAwaiting(error) {
        this.promisesAwaitingConnection.map(({ reject }) => reject(error));
        this.promisesAwaitingConnection = [];
    }
    awaitConnection() {
        return __awaiter(this, void 0, void 0, function* () {
            return new Promise((resolve, reject) => {
                this.promisesAwaitingConnection.push({ resolve, reject });
            });
        });
    }
}
exports["default"] = ConnectionManager;


/***/ }),

/***/ "./dist/npm/client/ExponentialBackoff.js":
/*!***********************************************!*\
  !*** ./dist/npm/client/ExponentialBackoff.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const DEFAULT_MIN = 100;
const DEFAULT_MAX = 1000;
class ExponentialBackoff {
    constructor(opts = {}) {
        var _a, _b;
        this.factor = 2;
        this.numAttempts = 0;
        this.ms = (_a = opts.min) !== null && _a !== void 0 ? _a : DEFAULT_MIN;
        this.max = (_b = opts.max) !== null && _b !== void 0 ? _b : DEFAULT_MAX;
    }
    get attempts() {
        return this.numAttempts;
    }
    duration() {
        const ms = this.ms * Math.pow(this.factor, this.numAttempts);
        this.numAttempts += 1;
        return Math.floor(Math.min(ms, this.max));
    }
    reset() {
        this.numAttempts = 0;
    }
}
exports["default"] = ExponentialBackoff;


/***/ }),

/***/ "./dist/npm/client/RequestManager.js":
/*!*******************************************!*\
  !*** ./dist/npm/client/RequestManager.js ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
class RequestManager {
    constructor() {
        this.nextId = 0;
        this.promisesAwaitingResponse = new Map();
    }
    addPromise(newId, timer) {
        return __awaiter(this, void 0, void 0, function* () {
            return new Promise((resolve, reject) => {
                this.promisesAwaitingResponse.set(newId, {
                    resolve,
                    reject,
                    timer,
                });
            });
        });
    }
    resolve(id, response) {
        const promise = this.promisesAwaitingResponse.get(id);
        if (promise == null) {
            throw new errors_1.XrplError(`No existing promise with id ${id}`, {
                type: 'resolve',
                response,
            });
        }
        clearTimeout(promise.timer);
        promise.resolve(response);
        this.deletePromise(id);
    }
    reject(id, error) {
        const promise = this.promisesAwaitingResponse.get(id);
        if (promise == null) {
            throw new errors_1.XrplError(`No existing promise with id ${id}`, {
                type: 'reject',
                error,
            });
        }
        clearTimeout(promise.timer);
        promise.reject(error);
        this.deletePromise(id);
    }
    rejectAll(error) {
        this.promisesAwaitingResponse.forEach((_promise, id, _map) => {
            this.reject(id, error);
            this.deletePromise(id);
        });
    }
    createRequest(request, timeout) {
        let newId;
        if (request.id == null) {
            newId = this.nextId;
            this.nextId += 1;
        }
        else {
            newId = request.id;
        }
        const newRequest = JSON.stringify(Object.assign(Object.assign({}, request), { id: newId }));
        const timer = setTimeout(() => {
            this.reject(newId, new errors_1.TimeoutError(`Timeout for request: ${JSON.stringify(request)} with id ${newId}`, request));
        }, timeout);
        if (timer.unref) {
            ;
            timer.unref();
        }
        if (this.promisesAwaitingResponse.has(newId)) {
            clearTimeout(timer);
            throw new errors_1.XrplError(`Response with id '${newId}' is already pending`, request);
        }
        const newPromise = new Promise((resolve, reject) => {
            this.promisesAwaitingResponse.set(newId, {
                resolve,
                reject,
                timer,
            });
        });
        return [newId, newRequest, newPromise];
    }
    handleResponse(response) {
        var _a, _b;
        if (response.id == null ||
            !(typeof response.id === 'string' || typeof response.id === 'number')) {
            throw new errors_1.ResponseFormatError('valid id not found in response', response);
        }
        if (!this.promisesAwaitingResponse.has(response.id)) {
            return;
        }
        if (response.status == null) {
            const error = new errors_1.ResponseFormatError('Response has no status');
            this.reject(response.id, error);
        }
        if (response.status === 'error') {
            const errorResponse = response;
            const error = new errors_1.RippledError((_a = errorResponse.error_message) !== null && _a !== void 0 ? _a : errorResponse.error, errorResponse);
            this.reject(response.id, error);
            return;
        }
        if (response.status !== 'success') {
            const error = new errors_1.ResponseFormatError(`unrecognized response.status: ${(_b = response.status) !== null && _b !== void 0 ? _b : ''}`, response);
            this.reject(response.id, error);
            return;
        }
        delete response.status;
        this.resolve(response.id, response);
    }
    deletePromise(id) {
        this.promisesAwaitingResponse.delete(id);
    }
}
exports["default"] = RequestManager;


/***/ }),

/***/ "./dist/npm/client/connection.js":
/*!***************************************!*\
  !*** ./dist/npm/client/connection.js ***!
  \***************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Connection = exports.INTENTIONAL_DISCONNECT_CODE = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const ws_1 = __importDefault(__webpack_require__(/*! @xrplf/isomorphic/ws */ "../../node_modules/@xrplf/isomorphic/dist/ws/browser.js"));
const eventemitter3_1 = __webpack_require__(/*! eventemitter3 */ "../../node_modules/eventemitter3/index.js");
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const ConnectionManager_1 = __importDefault(__webpack_require__(/*! ./ConnectionManager */ "./dist/npm/client/ConnectionManager.js"));
const ExponentialBackoff_1 = __importDefault(__webpack_require__(/*! ./ExponentialBackoff */ "./dist/npm/client/ExponentialBackoff.js"));
const RequestManager_1 = __importDefault(__webpack_require__(/*! ./RequestManager */ "./dist/npm/client/RequestManager.js"));
const SECONDS_PER_MINUTE = 60;
const TIMEOUT = 20;
const CONNECTION_TIMEOUT = 5;
exports.INTENTIONAL_DISCONNECT_CODE = 4000;
function createWebSocket(url, config) {
    const options = {
        agent: config.agent,
    };
    if (config.headers) {
        options.headers = config.headers;
    }
    if (config.authorization != null) {
        options.headers = Object.assign(Object.assign({}, options.headers), { Authorization: `Basic ${btoa(config.authorization)}` });
    }
    const websocketOptions = Object.assign({}, options);
    return new ws_1.default(url, websocketOptions);
}
function websocketSendAsync(ws, message) {
    return __awaiter(this, void 0, void 0, function* () {
        return new Promise((resolve, reject) => {
            ws.send(message, (error) => {
                if (error) {
                    reject(new errors_1.DisconnectedError(error.message, error));
                }
                else {
                    resolve();
                }
            });
        });
    });
}
class Connection extends eventemitter3_1.EventEmitter {
    constructor(url, options = {}) {
        super();
        this.ws = null;
        this.reconnectTimeoutID = null;
        this.heartbeatIntervalID = null;
        this.retryConnectionBackoff = new ExponentialBackoff_1.default({
            min: 100,
            max: SECONDS_PER_MINUTE * 1000,
        });
        this.requestManager = new RequestManager_1.default();
        this.connectionManager = new ConnectionManager_1.default();
        this.trace = () => { };
        this.url = url;
        this.config = Object.assign({ timeout: TIMEOUT * 1000, connectionTimeout: CONNECTION_TIMEOUT * 1000 }, options);
        if (typeof options.trace === 'function') {
            this.trace = options.trace;
        }
        else if (options.trace) {
            this.trace = console.log;
        }
    }
    get state() {
        return this.ws ? this.ws.readyState : ws_1.default.CLOSED;
    }
    get shouldBeConnected() {
        return this.ws !== null;
    }
    isConnected() {
        return this.state === ws_1.default.OPEN;
    }
    connect() {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.isConnected()) {
                return Promise.resolve();
            }
            if (this.state === ws_1.default.CONNECTING) {
                return this.connectionManager.awaitConnection();
            }
            if (!this.url) {
                return Promise.reject(new errors_1.ConnectionError('Cannot connect because no server was specified'));
            }
            if (this.ws != null) {
                return Promise.reject(new errors_1.XrplError('Websocket connection never cleaned up.', {
                    state: this.state,
                }));
            }
            const connectionTimeoutID = setTimeout(() => {
                this.onConnectionFailed(new errors_1.ConnectionError(`Error: connect() timed out after ${this.config.connectionTimeout} ms. If your internet connection is working, the ` +
                    `rippled server may be blocked or inaccessible. You can also try setting the 'connectionTimeout' option in the Client constructor.`));
            }, this.config.connectionTimeout);
            this.ws = createWebSocket(this.url, this.config);
            if (this.ws == null) {
                throw new errors_1.XrplError('Connect: created null websocket');
            }
            this.ws.on('error', (error) => this.onConnectionFailed(error));
            this.ws.on('error', () => clearTimeout(connectionTimeoutID));
            this.ws.on('close', (reason) => this.onConnectionFailed(reason));
            this.ws.on('close', () => clearTimeout(connectionTimeoutID));
            this.ws.once('open', () => {
                void this.onceOpen(connectionTimeoutID);
            });
            return this.connectionManager.awaitConnection();
        });
    }
    disconnect() {
        return __awaiter(this, void 0, void 0, function* () {
            this.clearHeartbeatInterval();
            if (this.reconnectTimeoutID !== null) {
                clearTimeout(this.reconnectTimeoutID);
                this.reconnectTimeoutID = null;
            }
            if (this.state === ws_1.default.CLOSED) {
                return Promise.resolve(undefined);
            }
            if (this.ws == null) {
                return Promise.resolve(undefined);
            }
            return new Promise((resolve) => {
                if (this.ws == null) {
                    resolve(undefined);
                }
                if (this.ws != null) {
                    this.ws.once('close', (code) => resolve(code));
                }
                if (this.ws != null && this.state !== ws_1.default.CLOSING) {
                    this.ws.close(exports.INTENTIONAL_DISCONNECT_CODE);
                }
            });
        });
    }
    reconnect() {
        return __awaiter(this, void 0, void 0, function* () {
            this.emit('reconnect');
            yield this.disconnect();
            yield this.connect();
        });
    }
    request(request, timeout) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!this.shouldBeConnected || this.ws == null) {
                throw new errors_1.NotConnectedError(JSON.stringify(request), request);
            }
            const [id, message, responsePromise] = this.requestManager.createRequest(request, timeout !== null && timeout !== void 0 ? timeout : this.config.timeout);
            this.trace('send', message);
            websocketSendAsync(this.ws, message).catch((error) => {
                this.requestManager.reject(id, error);
            });
            return responsePromise;
        });
    }
    getUrl() {
        var _a;
        return (_a = this.url) !== null && _a !== void 0 ? _a : '';
    }
    onMessage(message) {
        this.trace('receive', message);
        let data;
        try {
            data = JSON.parse(message);
        }
        catch (error) {
            if (error instanceof Error) {
                this.emit('error', 'badMessage', error.message, message);
            }
            return;
        }
        if (data.type == null && data.error) {
            this.emit('error', data.error, data.error_message, data);
            return;
        }
        if (data.type) {
            this.emit(data.type, data);
        }
        if (data.type === 'response') {
            try {
                this.requestManager.handleResponse(data);
            }
            catch (error) {
                if (error instanceof Error) {
                    this.emit('error', 'badMessage', error.message, message);
                }
                else {
                    this.emit('error', 'badMessage', error, error);
                }
            }
        }
    }
    onceOpen(connectionTimeoutID) {
        return __awaiter(this, void 0, void 0, function* () {
            if (this.ws == null) {
                throw new errors_1.XrplError('onceOpen: ws is null');
            }
            this.ws.removeAllListeners();
            clearTimeout(connectionTimeoutID);
            this.ws.on('message', (message) => this.onMessage(message));
            this.ws.on('error', (error) => this.emit('error', 'websocket', error.message, error));
            this.ws.once('close', (code, reason) => {
                if (this.ws == null) {
                    throw new errors_1.XrplError('onceClose: ws is null');
                }
                this.clearHeartbeatInterval();
                this.requestManager.rejectAll(new errors_1.DisconnectedError(`websocket was closed, ${reason ? (0, utils_1.hexToString)((0, utils_1.bytesToHex)(reason)) : ''}`));
                this.ws.removeAllListeners();
                this.ws = null;
                if (code === undefined) {
                    const internalErrorCode = 1011;
                    this.emit('disconnected', internalErrorCode);
                }
                else {
                    this.emit('disconnected', code);
                }
                if (code !== exports.INTENTIONAL_DISCONNECT_CODE && code !== undefined) {
                    this.intentionalDisconnect();
                }
            });
            try {
                this.retryConnectionBackoff.reset();
                this.startHeartbeatInterval();
                this.connectionManager.resolveAllAwaiting();
                this.emit('connected');
            }
            catch (error) {
                if (error instanceof Error) {
                    this.connectionManager.rejectAllAwaiting(error);
                    yield this.disconnect().catch(() => { });
                }
            }
        });
    }
    intentionalDisconnect() {
        const retryTimeout = this.retryConnectionBackoff.duration();
        this.trace('reconnect', `Retrying connection in ${retryTimeout}ms.`);
        this.emit('reconnecting', this.retryConnectionBackoff.attempts);
        this.reconnectTimeoutID = setTimeout(() => {
            this.reconnect().catch((error) => {
                this.emit('error', 'reconnect', error.message, error);
            });
        }, retryTimeout);
    }
    clearHeartbeatInterval() {
        if (this.heartbeatIntervalID) {
            clearInterval(this.heartbeatIntervalID);
        }
    }
    startHeartbeatInterval() {
        this.clearHeartbeatInterval();
        this.heartbeatIntervalID = setInterval(() => {
            void this.heartbeat();
        }, this.config.timeout);
    }
    heartbeat() {
        return __awaiter(this, void 0, void 0, function* () {
            this.request({ command: 'ping' }).catch(() => __awaiter(this, void 0, void 0, function* () {
                return this.reconnect().catch((error) => {
                    this.emit('error', 'reconnect', error.message, error);
                });
            }));
        });
    }
    onConnectionFailed(errorOrCode) {
        if (this.ws) {
            this.ws.removeAllListeners();
            this.ws.on('error', () => {
            });
            this.ws.close();
            this.ws = null;
        }
        if (typeof errorOrCode === 'number') {
            this.connectionManager.rejectAllAwaiting(new errors_1.NotConnectedError(`Connection failed with code ${errorOrCode}.`, {
                code: errorOrCode,
            }));
        }
        else if (errorOrCode === null || errorOrCode === void 0 ? void 0 : errorOrCode.message) {
            this.connectionManager.rejectAllAwaiting(new errors_1.NotConnectedError(errorOrCode.message, errorOrCode));
        }
        else {
            this.connectionManager.rejectAllAwaiting(new errors_1.NotConnectedError('Connection failed.'));
        }
    }
}
exports.Connection = Connection;


/***/ }),

/***/ "./dist/npm/client/index.js":
/*!**********************************!*\
  !*** ./dist/npm/client/index.js ***!
  \**********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Client = void 0;
const eventemitter3_1 = __webpack_require__(/*! eventemitter3 */ "../../node_modules/eventemitter3/index.js");
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ../models/common */ "./dist/npm/models/common/index.js");
const flags_1 = __webpack_require__(/*! ../models/utils/flags */ "./dist/npm/models/utils/flags.js");
const sugar_1 = __webpack_require__(/*! ../sugar */ "./dist/npm/sugar/index.js");
const autofill_1 = __webpack_require__(/*! ../sugar/autofill */ "./dist/npm/sugar/autofill.js");
const balances_1 = __webpack_require__(/*! ../sugar/balances */ "./dist/npm/sugar/balances.js");
const getOrderbook_1 = __webpack_require__(/*! ../sugar/getOrderbook */ "./dist/npm/sugar/getOrderbook.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/utils/index.js");
const Wallet_1 = __webpack_require__(/*! ../Wallet */ "./dist/npm/Wallet/index.js");
const fundWallet_1 = __webpack_require__(/*! ../Wallet/fundWallet */ "./dist/npm/Wallet/fundWallet.js");
const connection_1 = __webpack_require__(/*! ./connection */ "./dist/npm/client/connection.js");
const partialPayment_1 = __webpack_require__(/*! ./partialPayment */ "./dist/npm/client/partialPayment.js");
function getCollectKeyFromCommand(command) {
    switch (command) {
        case 'account_channels':
            return 'channels';
        case 'account_lines':
            return 'lines';
        case 'account_objects':
            return 'account_objects';
        case 'account_tx':
            return 'transactions';
        case 'account_offers':
        case 'book_offers':
            return 'offers';
        case 'ledger_data':
            return 'state';
        default:
            return null;
    }
}
function clamp(value, min, max) {
    if (min > max) {
        throw new Error('Illegal clamp bounds');
    }
    return Math.min(Math.max(value, min), max);
}
const DEFAULT_FEE_CUSHION = 1.2;
const DEFAULT_MAX_FEE_XRP = '2';
const MIN_LIMIT = 10;
const MAX_LIMIT = 400;
const NORMAL_DISCONNECT_CODE = 1000;
class Client extends eventemitter3_1.EventEmitter {
    constructor(server, options = {}) {
        var _a, _b;
        super();
        this.apiVersion = common_1.DEFAULT_API_VERSION;
        if (typeof server !== 'string' || !/wss?(?:\+unix)?:\/\//u.exec(server)) {
            throw new errors_1.ValidationError('server URI must start with `wss://`, `ws://`, `wss+unix://`, or `ws+unix://`.');
        }
        this.feeCushion = (_a = options.feeCushion) !== null && _a !== void 0 ? _a : DEFAULT_FEE_CUSHION;
        this.maxFeeXRP = (_b = options.maxFeeXRP) !== null && _b !== void 0 ? _b : DEFAULT_MAX_FEE_XRP;
        this.connection = new connection_1.Connection(server, options);
        this.connection.on('error', (errorCode, errorMessage, data) => {
            this.emit('error', errorCode, errorMessage, data);
        });
        this.connection.on('reconnect', () => {
            this.connection.on('connected', () => this.emit('connected'));
        });
        this.connection.on('disconnected', (code) => {
            let finalCode = code;
            if (finalCode === connection_1.INTENTIONAL_DISCONNECT_CODE) {
                finalCode = NORMAL_DISCONNECT_CODE;
            }
            this.emit('disconnected', finalCode);
        });
        this.connection.on('ledgerClosed', (ledger) => {
            this.emit('ledgerClosed', ledger);
        });
        this.connection.on('transaction', (tx) => {
            (0, partialPayment_1.handleStreamPartialPayment)(tx, this.connection.trace);
            this.emit('transaction', tx);
        });
        this.connection.on('validationReceived', (validation) => {
            this.emit('validationReceived', validation);
        });
        this.connection.on('manifestReceived', (manifest) => {
            this.emit('manifestReceived', manifest);
        });
        this.connection.on('peerStatusChange', (status) => {
            this.emit('peerStatusChange', status);
        });
        this.connection.on('consensusPhase', (consensus) => {
            this.emit('consensusPhase', consensus);
        });
        this.connection.on('path_find', (path) => {
            this.emit('path_find', path);
        });
    }
    get url() {
        return this.connection.getUrl();
    }
    request(req) {
        var _a;
        return __awaiter(this, void 0, void 0, function* () {
            const request = Object.assign(Object.assign({}, req), { account: typeof req.account === 'string'
                    ? (0, sugar_1.ensureClassicAddress)(req.account)
                    : undefined, api_version: (_a = req.api_version) !== null && _a !== void 0 ? _a : this.apiVersion });
            const response = yield this.connection.request(request);
            (0, partialPayment_1.handlePartialPayment)(req.command, response);
            return response;
        });
    }
    requestNextPage(req, resp) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!resp.result.marker) {
                return Promise.reject(new errors_1.NotFoundError('response does not have a next page'));
            }
            const nextPageRequest = Object.assign(Object.assign({}, req), { marker: resp.result.marker });
            return this.request(nextPageRequest);
        });
    }
    on(eventName, listener) {
        return super.on(eventName, listener);
    }
    requestAll(request, collect) {
        return __awaiter(this, void 0, void 0, function* () {
            const collectKey = collect !== null && collect !== void 0 ? collect : getCollectKeyFromCommand(request.command);
            if (!collectKey) {
                throw new errors_1.ValidationError(`no collect key for command ${request.command}`);
            }
            const countTo = request.limit == null ? Infinity : request.limit;
            let count = 0;
            let marker = request.marker;
            const results = [];
            do {
                const countRemaining = clamp(countTo - count, MIN_LIMIT, MAX_LIMIT);
                const repeatProps = Object.assign(Object.assign({}, request), { limit: countRemaining, marker });
                const singleResponse = yield this.connection.request(repeatProps);
                const singleResult = singleResponse.result;
                if (!(collectKey in singleResult)) {
                    throw new errors_1.XrplError(`${collectKey} not in result`);
                }
                const collectedData = singleResult[collectKey];
                marker = singleResult.marker;
                results.push(singleResponse);
                if (Array.isArray(collectedData)) {
                    count += collectedData.length;
                }
            } while (Boolean(marker) && count < countTo);
            return results;
        });
    }
    getServerInfo() {
        var _a;
        return __awaiter(this, void 0, void 0, function* () {
            try {
                const response = yield this.request({
                    command: 'server_info',
                });
                this.networkID = (_a = response.result.info.network_id) !== null && _a !== void 0 ? _a : undefined;
                this.buildVersion = response.result.info.build_version;
            }
            catch (error) {
                console.error(error);
            }
        });
    }
    connect() {
        return __awaiter(this, void 0, void 0, function* () {
            return this.connection.connect().then(() => __awaiter(this, void 0, void 0, function* () {
                yield this.getServerInfo();
                this.emit('connected');
            }));
        });
    }
    disconnect() {
        return __awaiter(this, void 0, void 0, function* () {
            yield this.connection.disconnect();
        });
    }
    isConnected() {
        return this.connection.isConnected();
    }
    autofill(transaction, signersCount) {
        return __awaiter(this, void 0, void 0, function* () {
            const tx = Object.assign({}, transaction);
            (0, autofill_1.setValidAddresses)(tx);
            tx.Flags = (0, flags_1.convertTxFlagsToNumber)(tx);
            const promises = [];
            if (tx.NetworkID == null) {
                tx.NetworkID = (0, autofill_1.txNeedsNetworkID)(this) ? this.networkID : undefined;
            }
            if (tx.Sequence == null) {
                promises.push((0, autofill_1.setNextValidSequenceNumber)(this, tx));
            }
            if (tx.Fee == null) {
                promises.push((0, autofill_1.calculateFeePerTransactionType)(this, tx, signersCount));
            }
            if (tx.LastLedgerSequence == null) {
                promises.push((0, autofill_1.setLatestValidatedLedgerSequence)(this, tx));
            }
            if (tx.TransactionType === 'AccountDelete') {
                promises.push((0, autofill_1.checkAccountDeleteBlockers)(this, tx));
            }
            if (tx.TransactionType === 'Payment' && tx.DeliverMax != null) {
                if (tx.Amount == null) {
                    tx.Amount = tx.DeliverMax;
                }
                if (tx.Amount != null && tx.Amount !== tx.DeliverMax) {
                    return Promise.reject(new errors_1.ValidationError('PaymentTransaction: Amount and DeliverMax fields must be identical when both are provided'));
                }
                delete tx.DeliverMax;
            }
            return Promise.all(promises).then(() => tx);
        });
    }
    submit(transaction, opts) {
        return __awaiter(this, void 0, void 0, function* () {
            const signedTx = yield (0, sugar_1.getSignedTx)(this, transaction, opts);
            return (0, sugar_1.submitRequest)(this, signedTx, opts === null || opts === void 0 ? void 0 : opts.failHard);
        });
    }
    simulate(transaction, opts) {
        var _a;
        return __awaiter(this, void 0, void 0, function* () {
            const binary = (_a = opts === null || opts === void 0 ? void 0 : opts.binary) !== null && _a !== void 0 ? _a : false;
            const request = typeof transaction === 'string'
                ? { command: 'simulate', tx_blob: transaction, binary }
                : { command: 'simulate', tx_json: transaction, binary };
            return this.request(request);
        });
    }
    submitAndWait(transaction, opts) {
        return __awaiter(this, void 0, void 0, function* () {
            const signedTx = yield (0, sugar_1.getSignedTx)(this, transaction, opts);
            const lastLedger = (0, sugar_1.getLastLedgerSequence)(signedTx);
            if (lastLedger == null) {
                throw new errors_1.ValidationError('Transaction must contain a LastLedgerSequence value for reliable submission.');
            }
            const response = yield (0, sugar_1.submitRequest)(this, signedTx, opts === null || opts === void 0 ? void 0 : opts.failHard);
            const txHash = utils_1.hashes.hashSignedTx(signedTx);
            return (0, sugar_1.waitForFinalTransactionOutcome)(this, txHash, lastLedger, response.result.engine_result);
        });
    }
    prepareTransaction(transaction, signersCount) {
        return __awaiter(this, void 0, void 0, function* () {
            return this.autofill(transaction, signersCount);
        });
    }
    getXrpBalance(address, options = {}) {
        var _a;
        return __awaiter(this, void 0, void 0, function* () {
            const xrpRequest = {
                command: 'account_info',
                account: address,
                ledger_index: (_a = options.ledger_index) !== null && _a !== void 0 ? _a : 'validated',
                ledger_hash: options.ledger_hash,
            };
            const response = yield this.request(xrpRequest);
            return (0, utils_1.dropsToXrp)(response.result.account_data.Balance);
        });
    }
    getBalances(address, options = {}) {
        var _a;
        return __awaiter(this, void 0, void 0, function* () {
            const balances = [];
            let xrpPromise = Promise.resolve(0);
            if (!options.peer) {
                xrpPromise = this.getXrpBalance(address, {
                    ledger_hash: options.ledger_hash,
                    ledger_index: options.ledger_index,
                });
            }
            const linesRequest = {
                command: 'account_lines',
                account: address,
                ledger_index: (_a = options.ledger_index) !== null && _a !== void 0 ? _a : 'validated',
                ledger_hash: options.ledger_hash,
                peer: options.peer,
                limit: options.limit,
            };
            const linesPromise = this.requestAll(linesRequest);
            yield Promise.all([xrpPromise, linesPromise]).then(([xrpBalance, linesResponses]) => {
                const accountLinesBalance = linesResponses.flatMap((response) => (0, balances_1.formatBalances)(response.result.lines));
                if (xrpBalance !== 0) {
                    balances.push({ currency: 'XRP', value: xrpBalance.toString() });
                }
                balances.push(...accountLinesBalance);
            });
            return balances.slice(0, options.limit);
        });
    }
    getOrderbook(currency1, currency2, options = {}) {
        return __awaiter(this, void 0, void 0, function* () {
            (0, getOrderbook_1.validateOrderbookOptions)(options);
            const request = (0, getOrderbook_1.createBookOffersRequest)(currency1, currency2, options);
            const directOfferResults = yield (0, getOrderbook_1.requestAllOffers)(this, request);
            const reverseOfferResults = yield (0, getOrderbook_1.requestAllOffers)(this, (0, getOrderbook_1.reverseRequest)(request));
            const directOffers = (0, getOrderbook_1.extractOffers)(directOfferResults);
            const reverseOffers = (0, getOrderbook_1.extractOffers)(reverseOfferResults);
            const orders = (0, getOrderbook_1.combineOrders)(directOffers, reverseOffers);
            const { buy, sell } = (0, getOrderbook_1.separateBuySellOrders)(orders);
            return {
                buy: (0, getOrderbook_1.sortAndLimitOffers)(buy, options.limit),
                sell: (0, getOrderbook_1.sortAndLimitOffers)(sell, options.limit),
            };
        });
    }
    getLedgerIndex() {
        return __awaiter(this, void 0, void 0, function* () {
            const ledgerResponse = yield this.request({
                command: 'ledger',
                ledger_index: 'validated',
            });
            return ledgerResponse.result.ledger_index;
        });
    }
    fundWallet(wallet, options = {}) {
        return __awaiter(this, void 0, void 0, function* () {
            if (!this.isConnected()) {
                throw new errors_1.RippledError('Client not connected, cannot call faucet');
            }
            const existingWallet = Boolean(wallet);
            const walletToFund = wallet && (0, utils_1.isValidClassicAddress)(wallet.classicAddress)
                ? wallet
                : Wallet_1.Wallet.generate();
            const postBody = {
                destination: walletToFund.classicAddress,
                xrpAmount: options.amount,
                usageContext: options.usageContext,
                userAgent: 'xrpl.js',
            };
            let startingBalance = 0;
            if (existingWallet) {
                try {
                    startingBalance = Number(yield this.getXrpBalance(walletToFund.classicAddress));
                }
                catch (_a) {
                }
            }
            return (0, fundWallet_1.requestFunding)(options, this, startingBalance, walletToFund, postBody);
        });
    }
}
exports.Client = Client;


/***/ }),

/***/ "./dist/npm/client/partialPayment.js":
/*!*******************************************!*\
  !*** ./dist/npm/client/partialPayment.js ***!
  \*******************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.handleStreamPartialPayment = exports.handlePartialPayment = void 0;
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const transactions_1 = __webpack_require__(/*! ../models/transactions */ "./dist/npm/models/transactions/index.js");
const utils_1 = __webpack_require__(/*! ../models/utils */ "./dist/npm/models/utils/index.js");
const WARN_PARTIAL_PAYMENT_CODE = 2001;
function amountsEqual(amt1, amt2) {
    if (typeof amt1 === 'string' && typeof amt2 === 'string') {
        return amt1 === amt2;
    }
    if (typeof amt1 === 'string' || typeof amt2 === 'string') {
        return false;
    }
    if ((0, transactions_1.isMPTAmount)(amt1) && (0, transactions_1.isMPTAmount)(amt2)) {
        const aValue = new bignumber_js_1.default(amt1.value);
        const bValue = new bignumber_js_1.default(amt2.value);
        return (amt1.mpt_issuance_id === amt2.mpt_issuance_id && aValue.isEqualTo(bValue));
    }
    if ((0, transactions_1.isMPTAmount)(amt1) || (0, transactions_1.isMPTAmount)(amt2)) {
        return false;
    }
    const aValue = new bignumber_js_1.default(amt1.value);
    const bValue = new bignumber_js_1.default(amt2.value);
    return (amt1.currency === amt2.currency &&
        amt1.issuer === amt2.issuer &&
        aValue.isEqualTo(bValue));
}
function isPartialPayment(tx, metadata) {
    var _a;
    if (tx == null || metadata == null || tx.TransactionType !== 'Payment') {
        return false;
    }
    let meta = metadata;
    if (typeof meta === 'string') {
        if (meta === 'unavailable') {
            return false;
        }
        meta = (0, ripple_binary_codec_1.decode)(meta);
    }
    const tfPartial = typeof tx.Flags === 'number'
        ? (0, utils_1.isFlagEnabled)(tx.Flags, transactions_1.PaymentFlags.tfPartialPayment)
        : (_a = tx.Flags) === null || _a === void 0 ? void 0 : _a.tfPartialPayment;
    if (!tfPartial) {
        return false;
    }
    const delivered = meta.delivered_amount;
    const amount = tx.DeliverMax;
    if (delivered === undefined) {
        return false;
    }
    return !amountsEqual(delivered, amount);
}
function txHasPartialPayment(response) {
    return isPartialPayment(response.result.tx_json, response.result.meta);
}
function txEntryHasPartialPayment(response) {
    return isPartialPayment(response.result.tx_json, response.result.metadata);
}
function accountTxHasPartialPayment(response) {
    const { transactions } = response.result;
    const foo = transactions.some((tx) => {
        if (tx.tx_json != null) {
            const transaction = tx;
            return isPartialPayment(transaction.tx_json, transaction.meta);
        }
        const transaction = tx;
        return isPartialPayment(transaction.tx, transaction.meta);
    });
    return foo;
}
function hasPartialPayment(command, response) {
    switch (command) {
        case 'tx':
            return txHasPartialPayment(response);
        case 'transaction_entry':
            return txEntryHasPartialPayment(response);
        case 'account_tx':
            return accountTxHasPartialPayment(response);
        default:
            return false;
    }
}
function handlePartialPayment(command, response) {
    var _a;
    if (hasPartialPayment(command, response)) {
        const warnings = (_a = response.warnings) !== null && _a !== void 0 ? _a : [];
        const warning = {
            id: WARN_PARTIAL_PAYMENT_CODE,
            message: 'This response contains a Partial Payment',
        };
        warnings.push(warning);
        response.warnings = warnings;
    }
}
exports.handlePartialPayment = handlePartialPayment;
function handleStreamPartialPayment(stream, log) {
    var _a, _b;
    if (isPartialPayment((_a = stream.tx_json) !== null && _a !== void 0 ? _a : stream.transaction, stream.meta)) {
        const warnings = (_b = stream.warnings) !== null && _b !== void 0 ? _b : [];
        const warning = {
            id: WARN_PARTIAL_PAYMENT_CODE,
            message: 'This response contains a Partial Payment',
        };
        warnings.push(warning);
        stream.warnings = warnings;
        log('Partial payment received', JSON.stringify(stream));
    }
}
exports.handleStreamPartialPayment = handleStreamPartialPayment;


/***/ }),

/***/ "./dist/npm/errors.js":
/*!****************************!*\
  !*** ./dist/npm/errors.js ***!
  \****************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.XRPLFaucetError = exports.NotFoundError = exports.ValidationError = exports.ResponseFormatError = exports.TimeoutError = exports.RippledNotInitializedError = exports.DisconnectedError = exports.NotConnectedError = exports.RippledError = exports.ConnectionError = exports.UnexpectedError = exports.XrplError = void 0;
class XrplError extends Error {
    constructor(message = '', data) {
        super(message);
        this.name = this.constructor.name;
        this.message = message;
        this.data = data;
        if (Error.captureStackTrace != null) {
            Error.captureStackTrace(this, this.constructor);
        }
    }
    toString() {
        let result = `[${this.name}(${this.message}`;
        if (this.data) {
            result += `, ${JSON.stringify(this.data)}`;
        }
        result += ')]';
        return result;
    }
    inspect() {
        return this.toString();
    }
}
exports.XrplError = XrplError;
class RippledError extends XrplError {
}
exports.RippledError = RippledError;
class UnexpectedError extends XrplError {
}
exports.UnexpectedError = UnexpectedError;
class ConnectionError extends XrplError {
}
exports.ConnectionError = ConnectionError;
class NotConnectedError extends ConnectionError {
}
exports.NotConnectedError = NotConnectedError;
class DisconnectedError extends ConnectionError {
}
exports.DisconnectedError = DisconnectedError;
class RippledNotInitializedError extends ConnectionError {
}
exports.RippledNotInitializedError = RippledNotInitializedError;
class TimeoutError extends ConnectionError {
}
exports.TimeoutError = TimeoutError;
class ResponseFormatError extends ConnectionError {
}
exports.ResponseFormatError = ResponseFormatError;
class ValidationError extends XrplError {
}
exports.ValidationError = ValidationError;
class XRPLFaucetError extends XrplError {
}
exports.XRPLFaucetError = XRPLFaucetError;
class NotFoundError extends XrplError {
    constructor(message = 'Not found') {
        super(message);
    }
}
exports.NotFoundError = NotFoundError;


/***/ }),

/***/ "./dist/npm/index.js":
/*!***************************!*\
  !*** ./dist/npm/index.js ***!
  \***************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.rfc1751MnemonicToKey = exports.keyToRFC1751Mnemonic = exports.walletFromSecretNumbers = exports.Wallet = exports.ECDSA = exports.Client = void 0;
var client_1 = __webpack_require__(/*! ./client */ "./dist/npm/client/index.js");
Object.defineProperty(exports, "Client", ({ enumerable: true, get: function () { return client_1.Client; } }));
__exportStar(__webpack_require__(/*! ./models */ "./dist/npm/models/index.js"), exports);
__exportStar(__webpack_require__(/*! ./utils */ "./dist/npm/utils/index.js"), exports);
var ECDSA_1 = __webpack_require__(/*! ./ECDSA */ "./dist/npm/ECDSA.js");
Object.defineProperty(exports, "ECDSA", ({ enumerable: true, get: function () { return __importDefault(ECDSA_1).default; } }));
__exportStar(__webpack_require__(/*! ./errors */ "./dist/npm/errors.js"), exports);
var Wallet_1 = __webpack_require__(/*! ./Wallet */ "./dist/npm/Wallet/index.js");
Object.defineProperty(exports, "Wallet", ({ enumerable: true, get: function () { return Wallet_1.Wallet; } }));
var walletFromSecretNumbers_1 = __webpack_require__(/*! ./Wallet/walletFromSecretNumbers */ "./dist/npm/Wallet/walletFromSecretNumbers.js");
Object.defineProperty(exports, "walletFromSecretNumbers", ({ enumerable: true, get: function () { return walletFromSecretNumbers_1.walletFromSecretNumbers; } }));
var rfc1751_1 = __webpack_require__(/*! ./Wallet/rfc1751 */ "./dist/npm/Wallet/rfc1751.js");
Object.defineProperty(exports, "keyToRFC1751Mnemonic", ({ enumerable: true, get: function () { return rfc1751_1.keyToRFC1751Mnemonic; } }));
Object.defineProperty(exports, "rfc1751MnemonicToKey", ({ enumerable: true, get: function () { return rfc1751_1.rfc1751MnemonicToKey; } }));
__exportStar(__webpack_require__(/*! ./Wallet/signer */ "./dist/npm/Wallet/signer.js"), exports);


/***/ }),

/***/ "./dist/npm/models/common/index.js":
/*!*****************************************!*\
  !*** ./dist/npm/models/common/index.js ***!
  \*****************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.DEFAULT_API_VERSION = exports.RIPPLED_API_V2 = exports.RIPPLED_API_V1 = void 0;
exports.RIPPLED_API_V1 = 1;
exports.RIPPLED_API_V2 = 2;
exports.DEFAULT_API_VERSION = exports.RIPPLED_API_V2;


/***/ }),

/***/ "./dist/npm/models/index.js":
/*!**********************************!*\
  !*** ./dist/npm/models/index.js ***!
  \**********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.parseTransactionFlags = exports.convertTxFlagsToNumber = exports.setTransactionFlagsToNumber = exports.parseAccountRootFlags = exports.LedgerEntry = void 0;
exports.LedgerEntry = __importStar(__webpack_require__(/*! ./ledger */ "./dist/npm/models/ledger/index.js"));
var flags_1 = __webpack_require__(/*! ./utils/flags */ "./dist/npm/models/utils/flags.js");
Object.defineProperty(exports, "parseAccountRootFlags", ({ enumerable: true, get: function () { return flags_1.parseAccountRootFlags; } }));
Object.defineProperty(exports, "setTransactionFlagsToNumber", ({ enumerable: true, get: function () { return flags_1.setTransactionFlagsToNumber; } }));
Object.defineProperty(exports, "convertTxFlagsToNumber", ({ enumerable: true, get: function () { return flags_1.convertTxFlagsToNumber; } }));
Object.defineProperty(exports, "parseTransactionFlags", ({ enumerable: true, get: function () { return flags_1.parseTransactionFlags; } }));
__exportStar(__webpack_require__(/*! ./methods */ "./dist/npm/models/methods/index.js"), exports);
__exportStar(__webpack_require__(/*! ./transactions */ "./dist/npm/models/transactions/index.js"), exports);
__exportStar(__webpack_require__(/*! ./common */ "./dist/npm/models/common/index.js"), exports);


/***/ }),

/***/ "./dist/npm/models/ledger/AccountRoot.js":
/*!***********************************************!*\
  !*** ./dist/npm/models/ledger/AccountRoot.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.AccountRootFlags = void 0;
var AccountRootFlags;
(function (AccountRootFlags) {
    AccountRootFlags[AccountRootFlags["lsfPasswordSpent"] = 65536] = "lsfPasswordSpent";
    AccountRootFlags[AccountRootFlags["lsfRequireDestTag"] = 131072] = "lsfRequireDestTag";
    AccountRootFlags[AccountRootFlags["lsfRequireAuth"] = 262144] = "lsfRequireAuth";
    AccountRootFlags[AccountRootFlags["lsfDisallowXRP"] = 524288] = "lsfDisallowXRP";
    AccountRootFlags[AccountRootFlags["lsfDisableMaster"] = 1048576] = "lsfDisableMaster";
    AccountRootFlags[AccountRootFlags["lsfNoFreeze"] = 2097152] = "lsfNoFreeze";
    AccountRootFlags[AccountRootFlags["lsfGlobalFreeze"] = 4194304] = "lsfGlobalFreeze";
    AccountRootFlags[AccountRootFlags["lsfDefaultRipple"] = 8388608] = "lsfDefaultRipple";
    AccountRootFlags[AccountRootFlags["lsfDepositAuth"] = 16777216] = "lsfDepositAuth";
    AccountRootFlags[AccountRootFlags["lsfAMM"] = 33554432] = "lsfAMM";
    AccountRootFlags[AccountRootFlags["lsfDisallowIncomingNFTokenOffer"] = 67108864] = "lsfDisallowIncomingNFTokenOffer";
    AccountRootFlags[AccountRootFlags["lsfDisallowIncomingCheck"] = 134217728] = "lsfDisallowIncomingCheck";
    AccountRootFlags[AccountRootFlags["lsfDisallowIncomingPayChan"] = 268435456] = "lsfDisallowIncomingPayChan";
    AccountRootFlags[AccountRootFlags["lsfDisallowIncomingTrustline"] = 536870912] = "lsfDisallowIncomingTrustline";
    AccountRootFlags[AccountRootFlags["lsfAllowTrustLineClawback"] = 2147483648] = "lsfAllowTrustLineClawback";
})(AccountRootFlags || (exports.AccountRootFlags = AccountRootFlags = {}));


/***/ }),

/***/ "./dist/npm/models/ledger/Amendments.js":
/*!**********************************************!*\
  !*** ./dist/npm/models/ledger/Amendments.js ***!
  \**********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.AMENDMENTS_ID = void 0;
exports.AMENDMENTS_ID = '7DB0788C020F02780A673DC74757F23823FA3014C1866E72CC4CD8B226CD6EF4';


/***/ }),

/***/ "./dist/npm/models/ledger/FeeSettings.js":
/*!***********************************************!*\
  !*** ./dist/npm/models/ledger/FeeSettings.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.FEE_SETTINGS_ID = void 0;
exports.FEE_SETTINGS_ID = '4BC50C9B0D8515D3EAAE1E74B29A95804346C491EE1A95BF25E4AAB854A6A651';


/***/ }),

/***/ "./dist/npm/models/ledger/NegativeUNL.js":
/*!***********************************************!*\
  !*** ./dist/npm/models/ledger/NegativeUNL.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.NEGATIVE_UNL_ID = void 0;
exports.NEGATIVE_UNL_ID = '2E8A59AA9D3B5B186B0B9E0F62E6C02587CA74A4D778938E957B6357D364B244';


/***/ }),

/***/ "./dist/npm/models/ledger/Offer.js":
/*!*****************************************!*\
  !*** ./dist/npm/models/ledger/Offer.js ***!
  \*****************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.OfferFlags = void 0;
var OfferFlags;
(function (OfferFlags) {
    OfferFlags[OfferFlags["lsfPassive"] = 65536] = "lsfPassive";
    OfferFlags[OfferFlags["lsfSell"] = 131072] = "lsfSell";
})(OfferFlags || (exports.OfferFlags = OfferFlags = {}));


/***/ }),

/***/ "./dist/npm/models/ledger/RippleState.js":
/*!***********************************************!*\
  !*** ./dist/npm/models/ledger/RippleState.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.RippleStateFlags = void 0;
var RippleStateFlags;
(function (RippleStateFlags) {
    RippleStateFlags[RippleStateFlags["lsfLowReserve"] = 65536] = "lsfLowReserve";
    RippleStateFlags[RippleStateFlags["lsfHighReserve"] = 131072] = "lsfHighReserve";
    RippleStateFlags[RippleStateFlags["lsfLowAuth"] = 262144] = "lsfLowAuth";
    RippleStateFlags[RippleStateFlags["lsfHighAuth"] = 524288] = "lsfHighAuth";
    RippleStateFlags[RippleStateFlags["lsfLowNoRipple"] = 1048576] = "lsfLowNoRipple";
    RippleStateFlags[RippleStateFlags["lsfHighNoRipple"] = 2097152] = "lsfHighNoRipple";
    RippleStateFlags[RippleStateFlags["lsfLowFreeze"] = 4194304] = "lsfLowFreeze";
    RippleStateFlags[RippleStateFlags["lsfHighFreeze"] = 8388608] = "lsfHighFreeze";
    RippleStateFlags[RippleStateFlags["lsfAMMNode"] = 16777216] = "lsfAMMNode";
    RippleStateFlags[RippleStateFlags["lsfLowDeepFreeze"] = 33554432] = "lsfLowDeepFreeze";
    RippleStateFlags[RippleStateFlags["lsfHighDeepFreeze"] = 67108864] = "lsfHighDeepFreeze";
})(RippleStateFlags || (exports.RippleStateFlags = RippleStateFlags = {}));


/***/ }),

/***/ "./dist/npm/models/ledger/SignerList.js":
/*!**********************************************!*\
  !*** ./dist/npm/models/ledger/SignerList.js ***!
  \**********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.SignerListFlags = void 0;
var SignerListFlags;
(function (SignerListFlags) {
    SignerListFlags[SignerListFlags["lsfOneOwnerCount"] = 65536] = "lsfOneOwnerCount";
})(SignerListFlags || (exports.SignerListFlags = SignerListFlags = {}));


/***/ }),

/***/ "./dist/npm/models/ledger/index.js":
/*!*****************************************!*\
  !*** ./dist/npm/models/ledger/index.js ***!
  \*****************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.SignerListFlags = exports.RippleStateFlags = exports.OfferFlags = exports.NEGATIVE_UNL_ID = exports.FEE_SETTINGS_ID = exports.AMENDMENTS_ID = exports.AccountRootFlags = void 0;
const AccountRoot_1 = __webpack_require__(/*! ./AccountRoot */ "./dist/npm/models/ledger/AccountRoot.js");
Object.defineProperty(exports, "AccountRootFlags", ({ enumerable: true, get: function () { return AccountRoot_1.AccountRootFlags; } }));
const Amendments_1 = __webpack_require__(/*! ./Amendments */ "./dist/npm/models/ledger/Amendments.js");
Object.defineProperty(exports, "AMENDMENTS_ID", ({ enumerable: true, get: function () { return Amendments_1.AMENDMENTS_ID; } }));
const FeeSettings_1 = __webpack_require__(/*! ./FeeSettings */ "./dist/npm/models/ledger/FeeSettings.js");
Object.defineProperty(exports, "FEE_SETTINGS_ID", ({ enumerable: true, get: function () { return FeeSettings_1.FEE_SETTINGS_ID; } }));
const NegativeUNL_1 = __webpack_require__(/*! ./NegativeUNL */ "./dist/npm/models/ledger/NegativeUNL.js");
Object.defineProperty(exports, "NEGATIVE_UNL_ID", ({ enumerable: true, get: function () { return NegativeUNL_1.NEGATIVE_UNL_ID; } }));
const Offer_1 = __webpack_require__(/*! ./Offer */ "./dist/npm/models/ledger/Offer.js");
Object.defineProperty(exports, "OfferFlags", ({ enumerable: true, get: function () { return Offer_1.OfferFlags; } }));
const RippleState_1 = __webpack_require__(/*! ./RippleState */ "./dist/npm/models/ledger/RippleState.js");
Object.defineProperty(exports, "RippleStateFlags", ({ enumerable: true, get: function () { return RippleState_1.RippleStateFlags; } }));
const SignerList_1 = __webpack_require__(/*! ./SignerList */ "./dist/npm/models/ledger/SignerList.js");
Object.defineProperty(exports, "SignerListFlags", ({ enumerable: true, get: function () { return SignerList_1.SignerListFlags; } }));


/***/ }),

/***/ "./dist/npm/models/methods/index.js":
/*!******************************************!*\
  !*** ./dist/npm/models/methods/index.js ***!
  \******************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));


/***/ }),

/***/ "./dist/npm/models/transactions/AMMBid.js":
/*!************************************************!*\
  !*** ./dist/npm/models/transactions/AMMBid.js ***!
  \************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMBid = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const MAX_AUTH_ACCOUNTS = 4;
function validateAMMBid(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Asset == null) {
        throw new errors_1.ValidationError('AMMBid: missing field Asset');
    }
    if (!(0, common_1.isCurrency)(tx.Asset)) {
        throw new errors_1.ValidationError('AMMBid: Asset must be a Currency');
    }
    if (tx.Asset2 == null) {
        throw new errors_1.ValidationError('AMMBid: missing field Asset2');
    }
    if (!(0, common_1.isCurrency)(tx.Asset2)) {
        throw new errors_1.ValidationError('AMMBid: Asset2 must be a Currency');
    }
    if (tx.BidMin != null && !(0, common_1.isAmount)(tx.BidMin)) {
        throw new errors_1.ValidationError('AMMBid: BidMin must be an Amount');
    }
    if (tx.BidMax != null && !(0, common_1.isAmount)(tx.BidMax)) {
        throw new errors_1.ValidationError('AMMBid: BidMax must be an Amount');
    }
    if (tx.AuthAccounts != null) {
        if (!Array.isArray(tx.AuthAccounts)) {
            throw new errors_1.ValidationError(`AMMBid: AuthAccounts must be an AuthAccount array`);
        }
        if (tx.AuthAccounts.length > MAX_AUTH_ACCOUNTS) {
            throw new errors_1.ValidationError(`AMMBid: AuthAccounts length must not be greater than ${MAX_AUTH_ACCOUNTS}`);
        }
        validateAuthAccounts(tx.Account, tx.AuthAccounts);
    }
}
exports.validateAMMBid = validateAMMBid;
function validateAuthAccounts(senderAddress, authAccounts) {
    for (const authAccount of authAccounts) {
        if (authAccount.AuthAccount == null ||
            typeof authAccount.AuthAccount !== 'object') {
            throw new errors_1.ValidationError(`AMMBid: invalid AuthAccounts`);
        }
        if (authAccount.AuthAccount.Account == null) {
            throw new errors_1.ValidationError(`AMMBid: invalid AuthAccounts`);
        }
        if (typeof authAccount.AuthAccount.Account !== 'string') {
            throw new errors_1.ValidationError(`AMMBid: invalid AuthAccounts`);
        }
        if (authAccount.AuthAccount.Account === senderAddress) {
            throw new errors_1.ValidationError(`AMMBid: AuthAccounts must not include sender's address`);
        }
    }
    return true;
}


/***/ }),

/***/ "./dist/npm/models/transactions/AMMClawback.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/AMMClawback.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMClawback = exports.AMMClawbackFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var AMMClawbackFlags;
(function (AMMClawbackFlags) {
    AMMClawbackFlags[AMMClawbackFlags["tfClawTwoAssets"] = 1] = "tfClawTwoAssets";
})(AMMClawbackFlags || (exports.AMMClawbackFlags = AMMClawbackFlags = {}));
function validateAMMClawback(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Holder', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'Asset', common_1.isCurrency);
    const asset = tx.Asset;
    const amount = tx.Amount;
    if (tx.Holder === asset.issuer) {
        throw new errors_1.ValidationError('AMMClawback: Holder and Asset.issuer must be distinct');
    }
    if (tx.Account !== asset.issuer) {
        throw new errors_1.ValidationError('AMMClawback: Account must be the same as Asset.issuer');
    }
    (0, common_1.validateRequiredField)(tx, 'Asset2', common_1.isCurrency);
    (0, common_1.validateOptionalField)(tx, 'Amount', common_1.isAmount);
    if (tx.Amount != null) {
        if (amount.currency !== asset.currency) {
            throw new errors_1.ValidationError('AMMClawback: Amount.currency must match Asset.currency');
        }
        if (amount.issuer !== asset.issuer) {
            throw new errors_1.ValidationError('AMMClawback: Amount.issuer must match Amount.issuer');
        }
    }
}
exports.validateAMMClawback = validateAMMClawback;


/***/ }),

/***/ "./dist/npm/models/transactions/AMMCreate.js":
/*!***************************************************!*\
  !*** ./dist/npm/models/transactions/AMMCreate.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMCreate = exports.AMM_MAX_TRADING_FEE = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
exports.AMM_MAX_TRADING_FEE = 1000;
function validateAMMCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Amount == null) {
        throw new errors_1.ValidationError('AMMCreate: missing field Amount');
    }
    if (!(0, common_1.isAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('AMMCreate: Amount must be an Amount');
    }
    if (tx.Amount2 == null) {
        throw new errors_1.ValidationError('AMMCreate: missing field Amount2');
    }
    if (!(0, common_1.isAmount)(tx.Amount2)) {
        throw new errors_1.ValidationError('AMMCreate: Amount2 must be an Amount');
    }
    if (tx.TradingFee == null) {
        throw new errors_1.ValidationError('AMMCreate: missing field TradingFee');
    }
    if (typeof tx.TradingFee !== 'number') {
        throw new errors_1.ValidationError('AMMCreate: TradingFee must be a number');
    }
    if (tx.TradingFee < 0 || tx.TradingFee > exports.AMM_MAX_TRADING_FEE) {
        throw new errors_1.ValidationError(`AMMCreate: TradingFee must be between 0 and ${exports.AMM_MAX_TRADING_FEE}`);
    }
}
exports.validateAMMCreate = validateAMMCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/AMMDelete.js":
/*!***************************************************!*\
  !*** ./dist/npm/models/transactions/AMMDelete.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMDelete = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateAMMDelete(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Asset == null) {
        throw new errors_1.ValidationError('AMMDelete: missing field Asset');
    }
    if (!(0, common_1.isCurrency)(tx.Asset)) {
        throw new errors_1.ValidationError('AMMDelete: Asset must be a Currency');
    }
    if (tx.Asset2 == null) {
        throw new errors_1.ValidationError('AMMDelete: missing field Asset2');
    }
    if (!(0, common_1.isCurrency)(tx.Asset2)) {
        throw new errors_1.ValidationError('AMMDelete: Asset2 must be a Currency');
    }
}
exports.validateAMMDelete = validateAMMDelete;


/***/ }),

/***/ "./dist/npm/models/transactions/AMMDeposit.js":
/*!****************************************************!*\
  !*** ./dist/npm/models/transactions/AMMDeposit.js ***!
  \****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMDeposit = exports.AMMDepositFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var AMMDepositFlags;
(function (AMMDepositFlags) {
    AMMDepositFlags[AMMDepositFlags["tfLPToken"] = 65536] = "tfLPToken";
    AMMDepositFlags[AMMDepositFlags["tfSingleAsset"] = 524288] = "tfSingleAsset";
    AMMDepositFlags[AMMDepositFlags["tfTwoAsset"] = 1048576] = "tfTwoAsset";
    AMMDepositFlags[AMMDepositFlags["tfOneAssetLPToken"] = 2097152] = "tfOneAssetLPToken";
    AMMDepositFlags[AMMDepositFlags["tfLimitLPToken"] = 4194304] = "tfLimitLPToken";
    AMMDepositFlags[AMMDepositFlags["tfTwoAssetIfEmpty"] = 8388608] = "tfTwoAssetIfEmpty";
})(AMMDepositFlags || (exports.AMMDepositFlags = AMMDepositFlags = {}));
function validateAMMDeposit(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Asset == null) {
        throw new errors_1.ValidationError('AMMDeposit: missing field Asset');
    }
    if (!(0, common_1.isCurrency)(tx.Asset)) {
        throw new errors_1.ValidationError('AMMDeposit: Asset must be a Currency');
    }
    if (tx.Asset2 == null) {
        throw new errors_1.ValidationError('AMMDeposit: missing field Asset2');
    }
    if (!(0, common_1.isCurrency)(tx.Asset2)) {
        throw new errors_1.ValidationError('AMMDeposit: Asset2 must be a Currency');
    }
    if (tx.Amount2 != null && tx.Amount == null) {
        throw new errors_1.ValidationError('AMMDeposit: must set Amount with Amount2');
    }
    else if (tx.EPrice != null && tx.Amount == null) {
        throw new errors_1.ValidationError('AMMDeposit: must set Amount with EPrice');
    }
    else if (tx.LPTokenOut == null && tx.Amount == null) {
        throw new errors_1.ValidationError('AMMDeposit: must set at least LPTokenOut or Amount');
    }
    if (tx.LPTokenOut != null && !(0, common_1.isIssuedCurrency)(tx.LPTokenOut)) {
        throw new errors_1.ValidationError('AMMDeposit: LPTokenOut must be an IssuedCurrencyAmount');
    }
    if (tx.Amount != null && !(0, common_1.isAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('AMMDeposit: Amount must be an Amount');
    }
    if (tx.Amount2 != null && !(0, common_1.isAmount)(tx.Amount2)) {
        throw new errors_1.ValidationError('AMMDeposit: Amount2 must be an Amount');
    }
    if (tx.EPrice != null && !(0, common_1.isAmount)(tx.EPrice)) {
        throw new errors_1.ValidationError('AMMDeposit: EPrice must be an Amount');
    }
}
exports.validateAMMDeposit = validateAMMDeposit;


/***/ }),

/***/ "./dist/npm/models/transactions/AMMVote.js":
/*!*************************************************!*\
  !*** ./dist/npm/models/transactions/AMMVote.js ***!
  \*************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMVote = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const AMMCreate_1 = __webpack_require__(/*! ./AMMCreate */ "./dist/npm/models/transactions/AMMCreate.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateAMMVote(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Asset == null) {
        throw new errors_1.ValidationError('AMMVote: missing field Asset');
    }
    if (!(0, common_1.isCurrency)(tx.Asset)) {
        throw new errors_1.ValidationError('AMMVote: Asset must be a Currency');
    }
    if (tx.Asset2 == null) {
        throw new errors_1.ValidationError('AMMVote: missing field Asset2');
    }
    if (!(0, common_1.isCurrency)(tx.Asset2)) {
        throw new errors_1.ValidationError('AMMVote: Asset2 must be a Currency');
    }
    if (tx.TradingFee == null) {
        throw new errors_1.ValidationError('AMMVote: missing field TradingFee');
    }
    if (typeof tx.TradingFee !== 'number') {
        throw new errors_1.ValidationError('AMMVote: TradingFee must be a number');
    }
    if (tx.TradingFee < 0 || tx.TradingFee > AMMCreate_1.AMM_MAX_TRADING_FEE) {
        throw new errors_1.ValidationError(`AMMVote: TradingFee must be between 0 and ${AMMCreate_1.AMM_MAX_TRADING_FEE}`);
    }
}
exports.validateAMMVote = validateAMMVote;


/***/ }),

/***/ "./dist/npm/models/transactions/AMMWithdraw.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/AMMWithdraw.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAMMWithdraw = exports.AMMWithdrawFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var AMMWithdrawFlags;
(function (AMMWithdrawFlags) {
    AMMWithdrawFlags[AMMWithdrawFlags["tfLPToken"] = 65536] = "tfLPToken";
    AMMWithdrawFlags[AMMWithdrawFlags["tfWithdrawAll"] = 131072] = "tfWithdrawAll";
    AMMWithdrawFlags[AMMWithdrawFlags["tfOneAssetWithdrawAll"] = 262144] = "tfOneAssetWithdrawAll";
    AMMWithdrawFlags[AMMWithdrawFlags["tfSingleAsset"] = 524288] = "tfSingleAsset";
    AMMWithdrawFlags[AMMWithdrawFlags["tfTwoAsset"] = 1048576] = "tfTwoAsset";
    AMMWithdrawFlags[AMMWithdrawFlags["tfOneAssetLPToken"] = 2097152] = "tfOneAssetLPToken";
    AMMWithdrawFlags[AMMWithdrawFlags["tfLimitLPToken"] = 4194304] = "tfLimitLPToken";
})(AMMWithdrawFlags || (exports.AMMWithdrawFlags = AMMWithdrawFlags = {}));
function validateAMMWithdraw(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Asset == null) {
        throw new errors_1.ValidationError('AMMWithdraw: missing field Asset');
    }
    if (!(0, common_1.isCurrency)(tx.Asset)) {
        throw new errors_1.ValidationError('AMMWithdraw: Asset must be a Currency');
    }
    if (tx.Asset2 == null) {
        throw new errors_1.ValidationError('AMMWithdraw: missing field Asset2');
    }
    if (!(0, common_1.isCurrency)(tx.Asset2)) {
        throw new errors_1.ValidationError('AMMWithdraw: Asset2 must be a Currency');
    }
    if (tx.Amount2 != null && tx.Amount == null) {
        throw new errors_1.ValidationError('AMMWithdraw: must set Amount with Amount2');
    }
    else if (tx.EPrice != null && tx.Amount == null) {
        throw new errors_1.ValidationError('AMMWithdraw: must set Amount with EPrice');
    }
    if (tx.LPTokenIn != null && !(0, common_1.isIssuedCurrency)(tx.LPTokenIn)) {
        throw new errors_1.ValidationError('AMMWithdraw: LPTokenIn must be an IssuedCurrencyAmount');
    }
    if (tx.Amount != null && !(0, common_1.isAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('AMMWithdraw: Amount must be an Amount');
    }
    if (tx.Amount2 != null && !(0, common_1.isAmount)(tx.Amount2)) {
        throw new errors_1.ValidationError('AMMWithdraw: Amount2 must be an Amount');
    }
    if (tx.EPrice != null && !(0, common_1.isAmount)(tx.EPrice)) {
        throw new errors_1.ValidationError('AMMWithdraw: EPrice must be an Amount');
    }
}
exports.validateAMMWithdraw = validateAMMWithdraw;


/***/ }),

/***/ "./dist/npm/models/transactions/CredentialAccept.js":
/*!**********************************************************!*\
  !*** ./dist/npm/models/transactions/CredentialAccept.js ***!
  \**********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateCredentialAccept = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateCredentialAccept(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Account', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'Issuer', common_1.isString);
    (0, common_1.validateCredentialType)(tx);
}
exports.validateCredentialAccept = validateCredentialAccept;


/***/ }),

/***/ "./dist/npm/models/transactions/CredentialCreate.js":
/*!**********************************************************!*\
  !*** ./dist/npm/models/transactions/CredentialCreate.js ***!
  \**********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateCredentialCreate = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const MAX_URI_LENGTH = 256;
function validateCredentialCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Account', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'Subject', common_1.isString);
    (0, common_1.validateCredentialType)(tx);
    (0, common_1.validateOptionalField)(tx, 'Expiration', common_1.isNumber);
    validateURI(tx.URI);
}
exports.validateCredentialCreate = validateCredentialCreate;
function validateURI(URI) {
    if (URI === undefined) {
        return;
    }
    if (typeof URI !== 'string') {
        throw new errors_1.ValidationError('CredentialCreate: invalid field URI');
    }
    if (URI.length === 0) {
        throw new errors_1.ValidationError('CredentialCreate: URI cannot be an empty string');
    }
    else if (URI.length > MAX_URI_LENGTH) {
        throw new errors_1.ValidationError(`CredentialCreate: URI length must be <= ${MAX_URI_LENGTH}`);
    }
    if (!utils_1.HEX_REGEX.test(URI)) {
        throw new errors_1.ValidationError('CredentialCreate: URI must be encoded in hex');
    }
}


/***/ }),

/***/ "./dist/npm/models/transactions/CredentialDelete.js":
/*!**********************************************************!*\
  !*** ./dist/npm/models/transactions/CredentialDelete.js ***!
  \**********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateCredentialDelete = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateCredentialDelete(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (!tx.Subject && !tx.Issuer) {
        throw new errors_1.ValidationError('CredentialDelete: either `Issuer` or `Subject` must be provided');
    }
    (0, common_1.validateRequiredField)(tx, 'Account', common_1.isString);
    (0, common_1.validateCredentialType)(tx);
    (0, common_1.validateOptionalField)(tx, 'Subject', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'Issuer', common_1.isString);
}
exports.validateCredentialDelete = validateCredentialDelete;


/***/ }),

/***/ "./dist/npm/models/transactions/DIDDelete.js":
/*!***************************************************!*\
  !*** ./dist/npm/models/transactions/DIDDelete.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateDIDDelete = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateDIDDelete(tx) {
    (0, common_1.validateBaseTransaction)(tx);
}
exports.validateDIDDelete = validateDIDDelete;


/***/ }),

/***/ "./dist/npm/models/transactions/DIDSet.js":
/*!************************************************!*\
  !*** ./dist/npm/models/transactions/DIDSet.js ***!
  \************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateDIDSet = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateDIDSet(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateOptionalField)(tx, 'Data', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'DIDDocument', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'URI', common_1.isString);
    if (tx.Data === undefined &&
        tx.DIDDocument === undefined &&
        tx.URI === undefined) {
        throw new errors_1.ValidationError('DIDSet: Must have at least one of `Data`, `DIDDocument`, and `URI`');
    }
}
exports.validateDIDSet = validateDIDSet;


/***/ }),

/***/ "./dist/npm/models/transactions/MPTokenAuthorize.js":
/*!**********************************************************!*\
  !*** ./dist/npm/models/transactions/MPTokenAuthorize.js ***!
  \**********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateMPTokenAuthorize = exports.MPTokenAuthorizeFlags = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var MPTokenAuthorizeFlags;
(function (MPTokenAuthorizeFlags) {
    MPTokenAuthorizeFlags[MPTokenAuthorizeFlags["tfMPTUnauthorize"] = 1] = "tfMPTUnauthorize";
})(MPTokenAuthorizeFlags || (exports.MPTokenAuthorizeFlags = MPTokenAuthorizeFlags = {}));
function validateMPTokenAuthorize(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'MPTokenIssuanceID', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'Holder', common_1.isAccount);
}
exports.validateMPTokenAuthorize = validateMPTokenAuthorize;


/***/ }),

/***/ "./dist/npm/models/transactions/MPTokenIssuanceCreate.js":
/*!***************************************************************!*\
  !*** ./dist/npm/models/transactions/MPTokenIssuanceCreate.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateMPTokenIssuanceCreate = exports.MPTokenIssuanceCreateFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const MAX_AMT = '9223372036854775807';
const MAX_TRANSFER_FEE = 50000;
var MPTokenIssuanceCreateFlags;
(function (MPTokenIssuanceCreateFlags) {
    MPTokenIssuanceCreateFlags[MPTokenIssuanceCreateFlags["tfMPTCanLock"] = 2] = "tfMPTCanLock";
    MPTokenIssuanceCreateFlags[MPTokenIssuanceCreateFlags["tfMPTRequireAuth"] = 4] = "tfMPTRequireAuth";
    MPTokenIssuanceCreateFlags[MPTokenIssuanceCreateFlags["tfMPTCanEscrow"] = 8] = "tfMPTCanEscrow";
    MPTokenIssuanceCreateFlags[MPTokenIssuanceCreateFlags["tfMPTCanTrade"] = 16] = "tfMPTCanTrade";
    MPTokenIssuanceCreateFlags[MPTokenIssuanceCreateFlags["tfMPTCanTransfer"] = 32] = "tfMPTCanTransfer";
    MPTokenIssuanceCreateFlags[MPTokenIssuanceCreateFlags["tfMPTCanClawback"] = 64] = "tfMPTCanClawback";
})(MPTokenIssuanceCreateFlags || (exports.MPTokenIssuanceCreateFlags = MPTokenIssuanceCreateFlags = {}));
function validateMPTokenIssuanceCreate(tx) {
    var _a;
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateOptionalField)(tx, 'MaximumAmount', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'MPTokenMetadata', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'TransferFee', common_1.isNumber);
    (0, common_1.validateOptionalField)(tx, 'AssetScale', common_1.isNumber);
    if (typeof tx.MPTokenMetadata === 'string' && tx.MPTokenMetadata === '') {
        throw new errors_1.ValidationError('MPTokenIssuanceCreate: MPTokenMetadata must not be empty string');
    }
    if (typeof tx.MPTokenMetadata === 'string' && !(0, utils_1.isHex)(tx.MPTokenMetadata)) {
        throw new errors_1.ValidationError('MPTokenIssuanceCreate: MPTokenMetadata must be in hex format');
    }
    if (typeof tx.MaximumAmount === 'string') {
        if (!utils_1.INTEGER_SANITY_CHECK.exec(tx.MaximumAmount)) {
            throw new errors_1.ValidationError('MPTokenIssuanceCreate: Invalid MaximumAmount');
        }
        else if (BigInt(tx.MaximumAmount) > BigInt(MAX_AMT) ||
            BigInt(tx.MaximumAmount) < BigInt(`0`)) {
            throw new errors_1.ValidationError('MPTokenIssuanceCreate: MaximumAmount out of range');
        }
    }
    if (typeof tx.TransferFee === 'number') {
        const flags = tx.Flags;
        const isTfMPTCanTransfer = typeof flags === 'number'
            ? (0, utils_1.isFlagEnabled)(flags, MPTokenIssuanceCreateFlags.tfMPTCanTransfer)
            : (_a = flags.tfMPTCanTransfer) !== null && _a !== void 0 ? _a : false;
        if (tx.TransferFee < 0 || tx.TransferFee > MAX_TRANSFER_FEE) {
            throw new errors_1.ValidationError(`MPTokenIssuanceCreate: TransferFee must be between 0 and ${MAX_TRANSFER_FEE}`);
        }
        if (tx.TransferFee && !isTfMPTCanTransfer) {
            throw new errors_1.ValidationError('MPTokenIssuanceCreate: TransferFee cannot be provided without enabling tfMPTCanTransfer flag');
        }
    }
}
exports.validateMPTokenIssuanceCreate = validateMPTokenIssuanceCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/MPTokenIssuanceDestroy.js":
/*!****************************************************************!*\
  !*** ./dist/npm/models/transactions/MPTokenIssuanceDestroy.js ***!
  \****************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateMPTokenIssuanceDestroy = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateMPTokenIssuanceDestroy(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'MPTokenIssuanceID', common_1.isString);
}
exports.validateMPTokenIssuanceDestroy = validateMPTokenIssuanceDestroy;


/***/ }),

/***/ "./dist/npm/models/transactions/MPTokenIssuanceSet.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/MPTokenIssuanceSet.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateMPTokenIssuanceSet = exports.MPTokenIssuanceSetFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var MPTokenIssuanceSetFlags;
(function (MPTokenIssuanceSetFlags) {
    MPTokenIssuanceSetFlags[MPTokenIssuanceSetFlags["tfMPTLock"] = 1] = "tfMPTLock";
    MPTokenIssuanceSetFlags[MPTokenIssuanceSetFlags["tfMPTUnlock"] = 2] = "tfMPTUnlock";
})(MPTokenIssuanceSetFlags || (exports.MPTokenIssuanceSetFlags = MPTokenIssuanceSetFlags = {}));
function validateMPTokenIssuanceSet(tx) {
    var _a, _b;
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'MPTokenIssuanceID', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'Holder', common_1.isAccount);
    const flags = tx.Flags;
    const isTfMPTLock = typeof flags === 'number'
        ? (0, utils_1.isFlagEnabled)(flags, MPTokenIssuanceSetFlags.tfMPTLock)
        : (_a = flags.tfMPTLock) !== null && _a !== void 0 ? _a : false;
    const isTfMPTUnlock = typeof flags === 'number'
        ? (0, utils_1.isFlagEnabled)(flags, MPTokenIssuanceSetFlags.tfMPTUnlock)
        : (_b = flags.tfMPTUnlock) !== null && _b !== void 0 ? _b : false;
    if (isTfMPTLock && isTfMPTUnlock) {
        throw new errors_1.ValidationError('MPTokenIssuanceSet: flag conflict');
    }
}
exports.validateMPTokenIssuanceSet = validateMPTokenIssuanceSet;


/***/ }),

/***/ "./dist/npm/models/transactions/NFTokenAcceptOffer.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/NFTokenAcceptOffer.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateNFTokenAcceptOffer = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateNFTokenBrokerFee(tx) {
    const value = (0, common_1.parseAmountValue)(tx.NFTokenBrokerFee);
    if (Number.isNaN(value)) {
        throw new errors_1.ValidationError('NFTokenAcceptOffer: invalid NFTokenBrokerFee');
    }
    if (value <= 0) {
        throw new errors_1.ValidationError('NFTokenAcceptOffer: NFTokenBrokerFee must be greater than 0; omit if there is no fee');
    }
    if (tx.NFTokenSellOffer == null || tx.NFTokenBuyOffer == null) {
        throw new errors_1.ValidationError('NFTokenAcceptOffer: both NFTokenSellOffer and NFTokenBuyOffer must be set if using brokered mode');
    }
}
function validateNFTokenAcceptOffer(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.NFTokenBrokerFee != null) {
        validateNFTokenBrokerFee(tx);
    }
    if (tx.NFTokenSellOffer == null && tx.NFTokenBuyOffer == null) {
        throw new errors_1.ValidationError('NFTokenAcceptOffer: must set either NFTokenSellOffer or NFTokenBuyOffer');
    }
}
exports.validateNFTokenAcceptOffer = validateNFTokenAcceptOffer;


/***/ }),

/***/ "./dist/npm/models/transactions/NFTokenBurn.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/NFTokenBurn.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateNFTokenBurn = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateNFTokenBurn(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'NFTokenID', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'Owner', common_1.isAccount);
}
exports.validateNFTokenBurn = validateNFTokenBurn;


/***/ }),

/***/ "./dist/npm/models/transactions/NFTokenCancelOffer.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/NFTokenCancelOffer.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateNFTokenCancelOffer = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateNFTokenCancelOffer(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (!Array.isArray(tx.NFTokenOffers)) {
        throw new errors_1.ValidationError('NFTokenCancelOffer: missing field NFTokenOffers');
    }
    if (tx.NFTokenOffers.length < 1) {
        throw new errors_1.ValidationError('NFTokenCancelOffer: empty field NFTokenOffers');
    }
}
exports.validateNFTokenCancelOffer = validateNFTokenCancelOffer;


/***/ }),

/***/ "./dist/npm/models/transactions/NFTokenCreateOffer.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/NFTokenCreateOffer.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateNFTokenCreateOffer = exports.NFTokenCreateOfferFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var NFTokenCreateOfferFlags;
(function (NFTokenCreateOfferFlags) {
    NFTokenCreateOfferFlags[NFTokenCreateOfferFlags["tfSellNFToken"] = 1] = "tfSellNFToken";
})(NFTokenCreateOfferFlags || (exports.NFTokenCreateOfferFlags = NFTokenCreateOfferFlags = {}));
function validateNFTokenSellOfferCases(tx) {
    if (tx.Owner != null) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: Owner must not be present for sell offers');
    }
}
function validateNFTokenBuyOfferCases(tx) {
    if (tx.Owner == null) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: Owner must be present for buy offers');
    }
    if ((0, common_1.parseAmountValue)(tx.Amount) <= 0) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: Amount must be greater than 0 for buy offers');
    }
}
function validateNFTokenCreateOffer(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Account === tx.Owner) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: Owner and Account must not be equal');
    }
    if (tx.Account === tx.Destination) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: Destination and Account must not be equal');
    }
    (0, common_1.validateOptionalField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'Owner', common_1.isAccount);
    if (tx.NFTokenID == null) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: missing field NFTokenID');
    }
    if (!(0, common_1.isAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('NFTokenCreateOffer: invalid Amount');
    }
    if (typeof tx.Flags === 'number' &&
        (0, utils_1.isFlagEnabled)(tx.Flags, NFTokenCreateOfferFlags.tfSellNFToken)) {
        validateNFTokenSellOfferCases(tx);
    }
    else {
        validateNFTokenBuyOfferCases(tx);
    }
}
exports.validateNFTokenCreateOffer = validateNFTokenCreateOffer;


/***/ }),

/***/ "./dist/npm/models/transactions/NFTokenMint.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/NFTokenMint.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateNFTokenMint = exports.NFTokenMintFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var NFTokenMintFlags;
(function (NFTokenMintFlags) {
    NFTokenMintFlags[NFTokenMintFlags["tfBurnable"] = 1] = "tfBurnable";
    NFTokenMintFlags[NFTokenMintFlags["tfOnlyXRP"] = 2] = "tfOnlyXRP";
    NFTokenMintFlags[NFTokenMintFlags["tfTrustLine"] = 4] = "tfTrustLine";
    NFTokenMintFlags[NFTokenMintFlags["tfTransferable"] = 8] = "tfTransferable";
    NFTokenMintFlags[NFTokenMintFlags["tfMutable"] = 16] = "tfMutable";
})(NFTokenMintFlags || (exports.NFTokenMintFlags = NFTokenMintFlags = {}));
function validateNFTokenMint(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Account === tx.Issuer) {
        throw new errors_1.ValidationError('NFTokenMint: Issuer must not be equal to Account');
    }
    (0, common_1.validateOptionalField)(tx, 'Issuer', common_1.isAccount);
    if (typeof tx.URI === 'string' && tx.URI === '') {
        throw new errors_1.ValidationError('NFTokenMint: URI must not be empty string');
    }
    if (typeof tx.URI === 'string' && !(0, utils_1.isHex)(tx.URI)) {
        throw new errors_1.ValidationError('NFTokenMint: URI must be in hex format');
    }
    if (tx.NFTokenTaxon == null) {
        throw new errors_1.ValidationError('NFTokenMint: missing field NFTokenTaxon');
    }
    if (tx.Amount == null) {
        if (tx.Expiration != null || tx.Destination != null) {
            throw new errors_1.ValidationError('NFTokenMint: Amount is required when Expiration or Destination is present');
        }
    }
    (0, common_1.validateOptionalField)(tx, 'Amount', common_1.isAmount);
    (0, common_1.validateOptionalField)(tx, 'Expiration', common_1.isNumber);
    (0, common_1.validateOptionalField)(tx, 'Destination', common_1.isAccount);
}
exports.validateNFTokenMint = validateNFTokenMint;


/***/ }),

/***/ "./dist/npm/models/transactions/NFTokenModify.js":
/*!*******************************************************!*\
  !*** ./dist/npm/models/transactions/NFTokenModify.js ***!
  \*******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateNFTokenModify = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateNFTokenModify(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'NFTokenID', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'Owner', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'URI', common_1.isString);
    if (tx.URI !== undefined && typeof tx.URI === 'string') {
        if (tx.URI === '') {
            throw new errors_1.ValidationError('NFTokenModify: URI must not be empty string');
        }
        if (!(0, utils_1.isHex)(tx.URI)) {
            throw new errors_1.ValidationError('NFTokenModify: URI must be in hex format');
        }
    }
}
exports.validateNFTokenModify = validateNFTokenModify;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainAccountCreateCommit.js":
/*!*******************************************************************!*\
  !*** ./dist/npm/models/transactions/XChainAccountCreateCommit.js ***!
  \*******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainAccountCreateCommit = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainAccountCreateCommit(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateRequiredField)(tx, 'SignatureReward', common_1.isAmount);
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'Amount', common_1.isAmount);
}
exports.validateXChainAccountCreateCommit = validateXChainAccountCreateCommit;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainAddAccountCreateAttestation.js":
/*!***************************************************************************!*\
  !*** ./dist/npm/models/transactions/XChainAddAccountCreateAttestation.js ***!
  \***************************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainAddAccountCreateAttestation = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainAddAccountCreateAttestation(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Amount', common_1.isAmount);
    (0, common_1.validateRequiredField)(tx, 'AttestationRewardAccount', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'AttestationSignerAccount', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'OtherChainSource', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'PublicKey', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'Signature', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'SignatureReward', common_1.isAmount);
    (0, common_1.validateRequiredField)(tx, 'WasLockingChainSend', (inp) => inp === 0 || inp === 1);
    (0, common_1.validateRequiredField)(tx, 'XChainAccountCreateCount', (inp) => (0, common_1.isNumber)(inp) || (0, common_1.isString)(inp));
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
}
exports.validateXChainAddAccountCreateAttestation = validateXChainAddAccountCreateAttestation;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainAddClaimAttestation.js":
/*!*******************************************************************!*\
  !*** ./dist/npm/models/transactions/XChainAddClaimAttestation.js ***!
  \*******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainAddClaimAttestation = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainAddClaimAttestation(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Amount', common_1.isAmount);
    (0, common_1.validateRequiredField)(tx, 'AttestationRewardAccount', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'AttestationSignerAccount', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'OtherChainSource', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'PublicKey', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'Signature', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'WasLockingChainSend', (inp) => inp === 0 || inp === 1);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateRequiredField)(tx, 'XChainClaimID', (inp) => (0, common_1.isNumber)(inp) || (0, common_1.isString)(inp));
}
exports.validateXChainAddClaimAttestation = validateXChainAddClaimAttestation;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainClaim.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/XChainClaim.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainClaim = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainClaim(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateRequiredField)(tx, 'XChainClaimID', (inp) => (0, common_1.isNumber)(inp) || (0, common_1.isString)(inp));
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'DestinationTag', common_1.isNumber);
    (0, common_1.validateRequiredField)(tx, 'Amount', common_1.isAmount);
}
exports.validateXChainClaim = validateXChainClaim;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainCommit.js":
/*!******************************************************!*\
  !*** ./dist/npm/models/transactions/XChainCommit.js ***!
  \******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainCommit = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainCommit(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateRequiredField)(tx, 'XChainClaimID', (inp) => (0, common_1.isNumber)(inp) || (0, common_1.isString)(inp));
    (0, common_1.validateOptionalField)(tx, 'OtherChainDestination', common_1.isAccount);
    (0, common_1.validateRequiredField)(tx, 'Amount', common_1.isAmount);
}
exports.validateXChainCommit = validateXChainCommit;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainCreateBridge.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/XChainCreateBridge.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainCreateBridge = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainCreateBridge(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateRequiredField)(tx, 'SignatureReward', common_1.isAmount);
    (0, common_1.validateOptionalField)(tx, 'MinAccountCreateAmount', common_1.isAmount);
}
exports.validateXChainCreateBridge = validateXChainCreateBridge;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainCreateClaimID.js":
/*!*************************************************************!*\
  !*** ./dist/npm/models/transactions/XChainCreateClaimID.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainCreateClaimID = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateXChainCreateClaimID(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateRequiredField)(tx, 'SignatureReward', common_1.isAmount);
    (0, common_1.validateRequiredField)(tx, 'OtherChainSource', common_1.isAccount);
}
exports.validateXChainCreateClaimID = validateXChainCreateClaimID;


/***/ }),

/***/ "./dist/npm/models/transactions/XChainModifyBridge.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/XChainModifyBridge.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateXChainModifyBridge = exports.XChainModifyBridgeFlags = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var XChainModifyBridgeFlags;
(function (XChainModifyBridgeFlags) {
    XChainModifyBridgeFlags[XChainModifyBridgeFlags["tfClearAccountCreateAmount"] = 65536] = "tfClearAccountCreateAmount";
})(XChainModifyBridgeFlags || (exports.XChainModifyBridgeFlags = XChainModifyBridgeFlags = {}));
function validateXChainModifyBridge(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'XChainBridge', common_1.isXChainBridge);
    (0, common_1.validateOptionalField)(tx, 'SignatureReward', common_1.isAmount);
    (0, common_1.validateOptionalField)(tx, 'MinAccountCreateAmount', common_1.isAmount);
}
exports.validateXChainModifyBridge = validateXChainModifyBridge;


/***/ }),

/***/ "./dist/npm/models/transactions/accountDelete.js":
/*!*******************************************************!*\
  !*** ./dist/npm/models/transactions/accountDelete.js ***!
  \*******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAccountDelete = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateAccountDelete(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'DestinationTag', common_1.isNumber);
    (0, common_1.validateCredentialsList)(tx.CredentialIDs, tx.TransactionType, true, common_1.MAX_AUTHORIZED_CREDENTIALS);
}
exports.validateAccountDelete = validateAccountDelete;


/***/ }),

/***/ "./dist/npm/models/transactions/accountSet.js":
/*!****************************************************!*\
  !*** ./dist/npm/models/transactions/accountSet.js ***!
  \****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateAccountSet = exports.AccountSetTfFlags = exports.AccountSetAsfFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var AccountSetAsfFlags;
(function (AccountSetAsfFlags) {
    AccountSetAsfFlags[AccountSetAsfFlags["asfRequireDest"] = 1] = "asfRequireDest";
    AccountSetAsfFlags[AccountSetAsfFlags["asfRequireAuth"] = 2] = "asfRequireAuth";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDisallowXRP"] = 3] = "asfDisallowXRP";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDisableMaster"] = 4] = "asfDisableMaster";
    AccountSetAsfFlags[AccountSetAsfFlags["asfAccountTxnID"] = 5] = "asfAccountTxnID";
    AccountSetAsfFlags[AccountSetAsfFlags["asfNoFreeze"] = 6] = "asfNoFreeze";
    AccountSetAsfFlags[AccountSetAsfFlags["asfGlobalFreeze"] = 7] = "asfGlobalFreeze";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDefaultRipple"] = 8] = "asfDefaultRipple";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDepositAuth"] = 9] = "asfDepositAuth";
    AccountSetAsfFlags[AccountSetAsfFlags["asfAuthorizedNFTokenMinter"] = 10] = "asfAuthorizedNFTokenMinter";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDisallowIncomingNFTokenOffer"] = 12] = "asfDisallowIncomingNFTokenOffer";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDisallowIncomingCheck"] = 13] = "asfDisallowIncomingCheck";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDisallowIncomingPayChan"] = 14] = "asfDisallowIncomingPayChan";
    AccountSetAsfFlags[AccountSetAsfFlags["asfDisallowIncomingTrustline"] = 15] = "asfDisallowIncomingTrustline";
    AccountSetAsfFlags[AccountSetAsfFlags["asfAllowTrustLineClawback"] = 16] = "asfAllowTrustLineClawback";
})(AccountSetAsfFlags || (exports.AccountSetAsfFlags = AccountSetAsfFlags = {}));
var AccountSetTfFlags;
(function (AccountSetTfFlags) {
    AccountSetTfFlags[AccountSetTfFlags["tfRequireDestTag"] = 65536] = "tfRequireDestTag";
    AccountSetTfFlags[AccountSetTfFlags["tfOptionalDestTag"] = 131072] = "tfOptionalDestTag";
    AccountSetTfFlags[AccountSetTfFlags["tfRequireAuth"] = 262144] = "tfRequireAuth";
    AccountSetTfFlags[AccountSetTfFlags["tfOptionalAuth"] = 524288] = "tfOptionalAuth";
    AccountSetTfFlags[AccountSetTfFlags["tfDisallowXRP"] = 1048576] = "tfDisallowXRP";
    AccountSetTfFlags[AccountSetTfFlags["tfAllowXRP"] = 2097152] = "tfAllowXRP";
})(AccountSetTfFlags || (exports.AccountSetTfFlags = AccountSetTfFlags = {}));
const MIN_TICK_SIZE = 3;
const MAX_TICK_SIZE = 15;
function validateAccountSet(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateOptionalField)(tx, 'NFTokenMinter', common_1.isAccount);
    if (tx.ClearFlag !== undefined) {
        if (typeof tx.ClearFlag !== 'number') {
            throw new errors_1.ValidationError('AccountSet: invalid ClearFlag');
        }
        if (!Object.values(AccountSetAsfFlags).includes(tx.ClearFlag)) {
            throw new errors_1.ValidationError('AccountSet: invalid ClearFlag');
        }
    }
    if (tx.Domain !== undefined && typeof tx.Domain !== 'string') {
        throw new errors_1.ValidationError('AccountSet: invalid Domain');
    }
    if (tx.EmailHash !== undefined && typeof tx.EmailHash !== 'string') {
        throw new errors_1.ValidationError('AccountSet: invalid EmailHash');
    }
    if (tx.MessageKey !== undefined && typeof tx.MessageKey !== 'string') {
        throw new errors_1.ValidationError('AccountSet: invalid MessageKey');
    }
    if (tx.SetFlag !== undefined) {
        if (typeof tx.SetFlag !== 'number') {
            throw new errors_1.ValidationError('AccountSet: invalid SetFlag');
        }
        if (!Object.values(AccountSetAsfFlags).includes(tx.SetFlag)) {
            throw new errors_1.ValidationError('AccountSet: invalid SetFlag');
        }
    }
    if (tx.TransferRate !== undefined && typeof tx.TransferRate !== 'number') {
        throw new errors_1.ValidationError('AccountSet: invalid TransferRate');
    }
    if (tx.TickSize !== undefined) {
        if (typeof tx.TickSize !== 'number') {
            throw new errors_1.ValidationError('AccountSet: invalid TickSize');
        }
        if (tx.TickSize !== 0 &&
            (tx.TickSize < MIN_TICK_SIZE || tx.TickSize > MAX_TICK_SIZE)) {
            throw new errors_1.ValidationError('AccountSet: invalid TickSize');
        }
    }
}
exports.validateAccountSet = validateAccountSet;


/***/ }),

/***/ "./dist/npm/models/transactions/checkCancel.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/checkCancel.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateCheckCancel = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateCheckCancel(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.CheckID !== undefined && typeof tx.CheckID !== 'string') {
        throw new errors_1.ValidationError('CheckCancel: invalid CheckID');
    }
}
exports.validateCheckCancel = validateCheckCancel;


/***/ }),

/***/ "./dist/npm/models/transactions/checkCash.js":
/*!***************************************************!*\
  !*** ./dist/npm/models/transactions/checkCash.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateCheckCash = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateCheckCash(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Amount == null && tx.DeliverMin == null) {
        throw new errors_1.ValidationError('CheckCash: must have either Amount or DeliverMin');
    }
    if (tx.Amount != null && tx.DeliverMin != null) {
        throw new errors_1.ValidationError('CheckCash: cannot have both Amount and DeliverMin');
    }
    if (tx.Amount != null && tx.Amount !== undefined && !(0, common_1.isAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('CheckCash: invalid Amount');
    }
    if (tx.DeliverMin != null &&
        tx.DeliverMin !== undefined &&
        !(0, common_1.isAmount)(tx.DeliverMin)) {
        throw new errors_1.ValidationError('CheckCash: invalid DeliverMin');
    }
    if (tx.CheckID !== undefined && typeof tx.CheckID !== 'string') {
        throw new errors_1.ValidationError('CheckCash: invalid CheckID');
    }
}
exports.validateCheckCash = validateCheckCash;


/***/ }),

/***/ "./dist/npm/models/transactions/checkCreate.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/checkCreate.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateCheckCreate = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateCheckCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.SendMax === undefined) {
        throw new errors_1.ValidationError('CheckCreate: missing field SendMax');
    }
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'DestinationTag', common_1.isNumber);
    if (typeof tx.SendMax !== 'string' &&
        !(0, common_1.isIssuedCurrency)(tx.SendMax)) {
        throw new errors_1.ValidationError('CheckCreate: invalid SendMax');
    }
    if (tx.Expiration !== undefined && typeof tx.Expiration !== 'number') {
        throw new errors_1.ValidationError('CheckCreate: invalid Expiration');
    }
    if (tx.InvoiceID !== undefined && typeof tx.InvoiceID !== 'string') {
        throw new errors_1.ValidationError('CheckCreate: invalid InvoiceID');
    }
}
exports.validateCheckCreate = validateCheckCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/clawback.js":
/*!**************************************************!*\
  !*** ./dist/npm/models/transactions/clawback.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateClawback = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateClawback(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateOptionalField)(tx, 'Holder', common_1.isAccount);
    if (tx.Amount == null) {
        throw new errors_1.ValidationError('Clawback: missing field Amount');
    }
    if (!(0, common_1.isIssuedCurrency)(tx.Amount) && !(0, common_1.isMPTAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('Clawback: invalid Amount');
    }
    if ((0, common_1.isIssuedCurrency)(tx.Amount) && tx.Account === tx.Amount.issuer) {
        throw new errors_1.ValidationError('Clawback: invalid holder Account');
    }
    if ((0, common_1.isMPTAmount)(tx.Amount) && tx.Account === tx.Holder) {
        throw new errors_1.ValidationError('Clawback: invalid holder Account');
    }
    if ((0, common_1.isIssuedCurrency)(tx.Amount) && tx.Holder) {
        throw new errors_1.ValidationError('Clawback: cannot have Holder for currency');
    }
    if ((0, common_1.isMPTAmount)(tx.Amount) && !tx.Holder) {
        throw new errors_1.ValidationError('Clawback: missing Holder');
    }
}
exports.validateClawback = validateClawback;


/***/ }),

/***/ "./dist/npm/models/transactions/common.js":
/*!************************************************!*\
  !*** ./dist/npm/models/transactions/common.js ***!
  \************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.containsDuplicates = exports.validateCredentialsList = exports.validateCredentialType = exports.parseAmountValue = exports.validateBaseTransaction = exports.validateOptionalField = exports.validateRequiredField = exports.isXChainBridge = exports.isAmount = exports.isAccount = exports.isMPTAmount = exports.isAuthorizeCredential = exports.isIssuedCurrency = exports.isCurrency = exports.isNumber = exports.isString = exports.MAX_AUTHORIZED_CREDENTIALS = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_2 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const MEMO_SIZE = 3;
exports.MAX_AUTHORIZED_CREDENTIALS = 8;
const MAX_CREDENTIAL_BYTE_LENGTH = 64;
const MAX_CREDENTIAL_TYPE_LENGTH = MAX_CREDENTIAL_BYTE_LENGTH * 2;
function isMemo(obj) {
    if (obj.Memo == null) {
        return false;
    }
    const memo = obj.Memo;
    const size = Object.keys(memo).length;
    const validData = memo.MemoData == null || typeof memo.MemoData === 'string';
    const validFormat = memo.MemoFormat == null || typeof memo.MemoFormat === 'string';
    const validType = memo.MemoType == null || typeof memo.MemoType === 'string';
    return (size >= 1 &&
        size <= MEMO_SIZE &&
        validData &&
        validFormat &&
        validType &&
        (0, utils_2.onlyHasFields)(memo, ['MemoFormat', 'MemoData', 'MemoType']));
}
const SIGNER_SIZE = 3;
function isSigner(obj) {
    const signerWrapper = obj;
    if (signerWrapper.Signer == null) {
        return false;
    }
    const signer = signerWrapper.Signer;
    return (Object.keys(signer).length === SIGNER_SIZE &&
        typeof signer.Account === 'string' &&
        typeof signer.TxnSignature === 'string' &&
        typeof signer.SigningPubKey === 'string');
}
const XRP_CURRENCY_SIZE = 1;
const ISSUE_SIZE = 2;
const ISSUED_CURRENCY_SIZE = 3;
const XCHAIN_BRIDGE_SIZE = 4;
const MPTOKEN_SIZE = 2;
const AUTHORIZE_CREDENTIAL_SIZE = 1;
function isRecord(value) {
    return value !== null && typeof value === 'object';
}
function isString(str) {
    return typeof str === 'string';
}
exports.isString = isString;
function isNumber(num) {
    return typeof num === 'number';
}
exports.isNumber = isNumber;
function isCurrency(input) {
    return (isRecord(input) &&
        ((Object.keys(input).length === ISSUE_SIZE &&
            typeof input.issuer === 'string' &&
            typeof input.currency === 'string') ||
            (Object.keys(input).length === XRP_CURRENCY_SIZE &&
                input.currency === 'XRP')));
}
exports.isCurrency = isCurrency;
function isIssuedCurrency(input) {
    return (isRecord(input) &&
        Object.keys(input).length === ISSUED_CURRENCY_SIZE &&
        typeof input.value === 'string' &&
        typeof input.issuer === 'string' &&
        typeof input.currency === 'string');
}
exports.isIssuedCurrency = isIssuedCurrency;
function isAuthorizeCredential(input) {
    return (isRecord(input) &&
        isRecord(input.Credential) &&
        Object.keys(input).length === AUTHORIZE_CREDENTIAL_SIZE &&
        typeof input.Credential.CredentialType === 'string' &&
        typeof input.Credential.Issuer === 'string');
}
exports.isAuthorizeCredential = isAuthorizeCredential;
function isMPTAmount(input) {
    return (isRecord(input) &&
        Object.keys(input).length === MPTOKEN_SIZE &&
        typeof input.value === 'string' &&
        typeof input.mpt_issuance_id === 'string');
}
exports.isMPTAmount = isMPTAmount;
function isAccount(account) {
    return (typeof account === 'string' &&
        ((0, ripple_address_codec_1.isValidClassicAddress)(account) || (0, ripple_address_codec_1.isValidXAddress)(account)));
}
exports.isAccount = isAccount;
function isAmount(amount) {
    return (typeof amount === 'string' ||
        isIssuedCurrency(amount) ||
        isMPTAmount(amount));
}
exports.isAmount = isAmount;
function isXChainBridge(input) {
    return (isRecord(input) &&
        Object.keys(input).length === XCHAIN_BRIDGE_SIZE &&
        typeof input.LockingChainDoor === 'string' &&
        isCurrency(input.LockingChainIssue) &&
        typeof input.IssuingChainDoor === 'string' &&
        isCurrency(input.IssuingChainIssue));
}
exports.isXChainBridge = isXChainBridge;
function validateRequiredField(tx, paramName, checkValidity) {
    if (tx[paramName] == null) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: missing field ${paramName}`);
    }
    if (!checkValidity(tx[paramName])) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: invalid field ${paramName}`);
    }
}
exports.validateRequiredField = validateRequiredField;
function validateOptionalField(tx, paramName, checkValidity) {
    if (tx[paramName] !== undefined && !checkValidity(tx[paramName])) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: invalid field ${paramName}`);
    }
}
exports.validateOptionalField = validateOptionalField;
function validateBaseTransaction(common) {
    if (common.TransactionType === undefined) {
        throw new errors_1.ValidationError('BaseTransaction: missing field TransactionType');
    }
    if (typeof common.TransactionType !== 'string') {
        throw new errors_1.ValidationError('BaseTransaction: TransactionType not string');
    }
    if (!ripple_binary_codec_1.TRANSACTION_TYPES.includes(common.TransactionType)) {
        throw new errors_1.ValidationError('BaseTransaction: Unknown TransactionType');
    }
    validateRequiredField(common, 'Account', isString);
    validateOptionalField(common, 'Fee', isString);
    validateOptionalField(common, 'Sequence', isNumber);
    validateOptionalField(common, 'AccountTxnID', isString);
    validateOptionalField(common, 'LastLedgerSequence', isNumber);
    const memos = common.Memos;
    if (memos !== undefined && !memos.every(isMemo)) {
        throw new errors_1.ValidationError('BaseTransaction: invalid Memos');
    }
    const signers = common.Signers;
    if (signers !== undefined &&
        (signers.length === 0 || !signers.every(isSigner))) {
        throw new errors_1.ValidationError('BaseTransaction: invalid Signers');
    }
    validateOptionalField(common, 'SourceTag', isNumber);
    validateOptionalField(common, 'SigningPubKey', isString);
    validateOptionalField(common, 'TicketSequence', isNumber);
    validateOptionalField(common, 'TxnSignature', isString);
    validateOptionalField(common, 'NetworkID', isNumber);
}
exports.validateBaseTransaction = validateBaseTransaction;
function parseAmountValue(amount) {
    if (!isAmount(amount)) {
        return NaN;
    }
    if (typeof amount === 'string') {
        return parseFloat(amount);
    }
    return parseFloat(amount.value);
}
exports.parseAmountValue = parseAmountValue;
function validateCredentialType(tx) {
    if (typeof tx.TransactionType !== 'string') {
        throw new errors_1.ValidationError('Invalid TransactionType');
    }
    if (tx.CredentialType === undefined) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: missing field CredentialType`);
    }
    if (!isString(tx.CredentialType)) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: CredentialType must be a string`);
    }
    if (tx.CredentialType.length === 0) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: CredentialType cannot be an empty string`);
    }
    else if (tx.CredentialType.length > MAX_CREDENTIAL_TYPE_LENGTH) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: CredentialType length cannot be > ${MAX_CREDENTIAL_TYPE_LENGTH}`);
    }
    if (!utils_1.HEX_REGEX.test(tx.CredentialType)) {
        throw new errors_1.ValidationError(`${tx.TransactionType}: CredentialType must be encoded in hex`);
    }
}
exports.validateCredentialType = validateCredentialType;
function validateCredentialsList(credentials, transactionType, isStringID, maxCredentials) {
    if (credentials == null) {
        return;
    }
    if (!Array.isArray(credentials)) {
        throw new errors_1.ValidationError(`${transactionType}: Credentials must be an array`);
    }
    if (credentials.length > maxCredentials) {
        throw new errors_1.ValidationError(`${transactionType}: Credentials length cannot exceed ${maxCredentials} elements`);
    }
    else if (credentials.length === 0) {
        throw new errors_1.ValidationError(`${transactionType}: Credentials cannot be an empty array`);
    }
    credentials.forEach((credential) => {
        if (isStringID) {
            if (!isString(credential)) {
                throw new errors_1.ValidationError(`${transactionType}: Invalid Credentials ID list format`);
            }
        }
        else if (!isAuthorizeCredential(credential)) {
            throw new errors_1.ValidationError(`${transactionType}: Invalid Credentials format`);
        }
    });
    if (containsDuplicates(credentials)) {
        throw new errors_1.ValidationError(`${transactionType}: Credentials cannot contain duplicate elements`);
    }
}
exports.validateCredentialsList = validateCredentialsList;
function isAuthorizeCredentialArray(list) {
    return typeof list[0] !== 'string';
}
function containsDuplicates(objectList) {
    if (typeof objectList[0] === 'string') {
        const objSet = new Set(objectList.map((obj) => JSON.stringify(obj)));
        return objSet.size !== objectList.length;
    }
    const seen = new Set();
    if (isAuthorizeCredentialArray(objectList)) {
        for (const item of objectList) {
            const key = `${item.Credential.Issuer}-${item.Credential.CredentialType}`;
            if (seen.has(key)) {
                return true;
            }
            seen.add(key);
        }
    }
    return false;
}
exports.containsDuplicates = containsDuplicates;


/***/ }),

/***/ "./dist/npm/models/transactions/depositPreauth.js":
/*!********************************************************!*\
  !*** ./dist/npm/models/transactions/depositPreauth.js ***!
  \********************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateDepositPreauth = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateDepositPreauth(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    validateSingleAuthorizationFieldProvided(tx);
    if (tx.Authorize !== undefined) {
        if (typeof tx.Authorize !== 'string') {
            throw new errors_1.ValidationError('DepositPreauth: Authorize must be a string');
        }
        if (tx.Account === tx.Authorize) {
            throw new errors_1.ValidationError("DepositPreauth: Account can't preauthorize its own address");
        }
    }
    else if (tx.Unauthorize !== undefined) {
        if (typeof tx.Unauthorize !== 'string') {
            throw new errors_1.ValidationError('DepositPreauth: Unauthorize must be a string');
        }
        if (tx.Account === tx.Unauthorize) {
            throw new errors_1.ValidationError("DepositPreauth: Account can't unauthorize its own address");
        }
    }
    else if (tx.AuthorizeCredentials !== undefined) {
        (0, common_1.validateCredentialsList)(tx.AuthorizeCredentials, tx.TransactionType, false, common_1.MAX_AUTHORIZED_CREDENTIALS);
    }
    else if (tx.UnauthorizeCredentials !== undefined) {
        (0, common_1.validateCredentialsList)(tx.UnauthorizeCredentials, tx.TransactionType, false, common_1.MAX_AUTHORIZED_CREDENTIALS);
    }
}
exports.validateDepositPreauth = validateDepositPreauth;
function validateSingleAuthorizationFieldProvided(tx) {
    const fields = [
        'Authorize',
        'Unauthorize',
        'AuthorizeCredentials',
        'UnauthorizeCredentials',
    ];
    const countProvided = fields.filter((key) => tx[key] !== undefined).length;
    if (countProvided !== 1) {
        throw new errors_1.ValidationError('DepositPreauth: Requires exactly one field of the following: Authorize, Unauthorize, AuthorizeCredentials, UnauthorizeCredentials.');
    }
}


/***/ }),

/***/ "./dist/npm/models/transactions/enableAmendment.js":
/*!*********************************************************!*\
  !*** ./dist/npm/models/transactions/enableAmendment.js ***!
  \*********************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.EnableAmendmentFlags = void 0;
var EnableAmendmentFlags;
(function (EnableAmendmentFlags) {
    EnableAmendmentFlags[EnableAmendmentFlags["tfGotMajority"] = 65536] = "tfGotMajority";
    EnableAmendmentFlags[EnableAmendmentFlags["tfLostMajority"] = 131072] = "tfLostMajority";
})(EnableAmendmentFlags || (exports.EnableAmendmentFlags = EnableAmendmentFlags = {}));


/***/ }),

/***/ "./dist/npm/models/transactions/escrowCancel.js":
/*!******************************************************!*\
  !*** ./dist/npm/models/transactions/escrowCancel.js ***!
  \******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateEscrowCancel = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateEscrowCancel(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Owner', common_1.isAccount);
    if (tx.OfferSequence == null) {
        throw new errors_1.ValidationError('EscrowCancel: missing OfferSequence');
    }
    if ((typeof tx.OfferSequence !== 'number' &&
        typeof tx.OfferSequence !== 'string') ||
        Number.isNaN(Number(tx.OfferSequence))) {
        throw new errors_1.ValidationError('EscrowCancel: OfferSequence must be a number');
    }
}
exports.validateEscrowCancel = validateEscrowCancel;


/***/ }),

/***/ "./dist/npm/models/transactions/escrowCreate.js":
/*!******************************************************!*\
  !*** ./dist/npm/models/transactions/escrowCreate.js ***!
  \******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateEscrowCreate = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateEscrowCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Amount === undefined) {
        throw new errors_1.ValidationError('EscrowCreate: missing field Amount');
    }
    if (typeof tx.Amount !== 'string') {
        throw new errors_1.ValidationError('EscrowCreate: Amount must be a string');
    }
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'DestinationTag', common_1.isNumber);
    if (tx.CancelAfter === undefined && tx.FinishAfter === undefined) {
        throw new errors_1.ValidationError('EscrowCreate: Either CancelAfter or FinishAfter must be specified');
    }
    if (tx.FinishAfter === undefined && tx.Condition === undefined) {
        throw new errors_1.ValidationError('EscrowCreate: Either Condition or FinishAfter must be specified');
    }
    if (tx.CancelAfter !== undefined && typeof tx.CancelAfter !== 'number') {
        throw new errors_1.ValidationError('EscrowCreate: CancelAfter must be a number');
    }
    if (tx.FinishAfter !== undefined && typeof tx.FinishAfter !== 'number') {
        throw new errors_1.ValidationError('EscrowCreate: FinishAfter must be a number');
    }
    if (tx.Condition !== undefined && typeof tx.Condition !== 'string') {
        throw new errors_1.ValidationError('EscrowCreate: Condition must be a string');
    }
}
exports.validateEscrowCreate = validateEscrowCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/escrowFinish.js":
/*!******************************************************!*\
  !*** ./dist/npm/models/transactions/escrowFinish.js ***!
  \******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateEscrowFinish = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateEscrowFinish(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'Owner', common_1.isAccount);
    (0, common_1.validateCredentialsList)(tx.CredentialIDs, tx.TransactionType, true, common_1.MAX_AUTHORIZED_CREDENTIALS);
    if (tx.OfferSequence == null) {
        throw new errors_1.ValidationError('EscrowFinish: missing field OfferSequence');
    }
    if ((typeof tx.OfferSequence !== 'number' &&
        typeof tx.OfferSequence !== 'string') ||
        Number.isNaN(Number(tx.OfferSequence))) {
        throw new errors_1.ValidationError('EscrowFinish: OfferSequence must be a number');
    }
    if (tx.Condition !== undefined && typeof tx.Condition !== 'string') {
        throw new errors_1.ValidationError('EscrowFinish: Condition must be a string');
    }
    if (tx.Fulfillment !== undefined && typeof tx.Fulfillment !== 'string') {
        throw new errors_1.ValidationError('EscrowFinish: Fulfillment must be a string');
    }
}
exports.validateEscrowFinish = validateEscrowFinish;


/***/ }),

/***/ "./dist/npm/models/transactions/index.js":
/*!***********************************************!*\
  !*** ./dist/npm/models/transactions/index.js ***!
  \***********************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.XChainModifyBridgeFlags = exports.TrustSetFlags = exports.PaymentChannelClaimFlags = exports.PaymentFlags = exports.OfferCreateFlags = exports.validateNFTokenModify = exports.NFTokenMintFlags = exports.NFTokenCreateOfferFlags = exports.MPTokenIssuanceSetFlags = exports.MPTokenIssuanceCreateFlags = exports.MPTokenAuthorizeFlags = exports.EnableAmendmentFlags = exports.AMMWithdrawFlags = exports.AMMDepositFlags = exports.AMMClawbackFlags = exports.AccountSetTfFlags = exports.AccountSetAsfFlags = exports.validate = exports.isMPTAmount = void 0;
var common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
Object.defineProperty(exports, "isMPTAmount", ({ enumerable: true, get: function () { return common_1.isMPTAmount; } }));
var transaction_1 = __webpack_require__(/*! ./transaction */ "./dist/npm/models/transactions/transaction.js");
Object.defineProperty(exports, "validate", ({ enumerable: true, get: function () { return transaction_1.validate; } }));
__exportStar(__webpack_require__(/*! ./metadata */ "./dist/npm/models/transactions/metadata.js"), exports);
var accountSet_1 = __webpack_require__(/*! ./accountSet */ "./dist/npm/models/transactions/accountSet.js");
Object.defineProperty(exports, "AccountSetAsfFlags", ({ enumerable: true, get: function () { return accountSet_1.AccountSetAsfFlags; } }));
Object.defineProperty(exports, "AccountSetTfFlags", ({ enumerable: true, get: function () { return accountSet_1.AccountSetTfFlags; } }));
var AMMClawback_1 = __webpack_require__(/*! ./AMMClawback */ "./dist/npm/models/transactions/AMMClawback.js");
Object.defineProperty(exports, "AMMClawbackFlags", ({ enumerable: true, get: function () { return AMMClawback_1.AMMClawbackFlags; } }));
var AMMDeposit_1 = __webpack_require__(/*! ./AMMDeposit */ "./dist/npm/models/transactions/AMMDeposit.js");
Object.defineProperty(exports, "AMMDepositFlags", ({ enumerable: true, get: function () { return AMMDeposit_1.AMMDepositFlags; } }));
var AMMWithdraw_1 = __webpack_require__(/*! ./AMMWithdraw */ "./dist/npm/models/transactions/AMMWithdraw.js");
Object.defineProperty(exports, "AMMWithdrawFlags", ({ enumerable: true, get: function () { return AMMWithdraw_1.AMMWithdrawFlags; } }));
var enableAmendment_1 = __webpack_require__(/*! ./enableAmendment */ "./dist/npm/models/transactions/enableAmendment.js");
Object.defineProperty(exports, "EnableAmendmentFlags", ({ enumerable: true, get: function () { return enableAmendment_1.EnableAmendmentFlags; } }));
var MPTokenAuthorize_1 = __webpack_require__(/*! ./MPTokenAuthorize */ "./dist/npm/models/transactions/MPTokenAuthorize.js");
Object.defineProperty(exports, "MPTokenAuthorizeFlags", ({ enumerable: true, get: function () { return MPTokenAuthorize_1.MPTokenAuthorizeFlags; } }));
var MPTokenIssuanceCreate_1 = __webpack_require__(/*! ./MPTokenIssuanceCreate */ "./dist/npm/models/transactions/MPTokenIssuanceCreate.js");
Object.defineProperty(exports, "MPTokenIssuanceCreateFlags", ({ enumerable: true, get: function () { return MPTokenIssuanceCreate_1.MPTokenIssuanceCreateFlags; } }));
var MPTokenIssuanceSet_1 = __webpack_require__(/*! ./MPTokenIssuanceSet */ "./dist/npm/models/transactions/MPTokenIssuanceSet.js");
Object.defineProperty(exports, "MPTokenIssuanceSetFlags", ({ enumerable: true, get: function () { return MPTokenIssuanceSet_1.MPTokenIssuanceSetFlags; } }));
var NFTokenCreateOffer_1 = __webpack_require__(/*! ./NFTokenCreateOffer */ "./dist/npm/models/transactions/NFTokenCreateOffer.js");
Object.defineProperty(exports, "NFTokenCreateOfferFlags", ({ enumerable: true, get: function () { return NFTokenCreateOffer_1.NFTokenCreateOfferFlags; } }));
var NFTokenMint_1 = __webpack_require__(/*! ./NFTokenMint */ "./dist/npm/models/transactions/NFTokenMint.js");
Object.defineProperty(exports, "NFTokenMintFlags", ({ enumerable: true, get: function () { return NFTokenMint_1.NFTokenMintFlags; } }));
var NFTokenModify_1 = __webpack_require__(/*! ./NFTokenModify */ "./dist/npm/models/transactions/NFTokenModify.js");
Object.defineProperty(exports, "validateNFTokenModify", ({ enumerable: true, get: function () { return NFTokenModify_1.validateNFTokenModify; } }));
var offerCreate_1 = __webpack_require__(/*! ./offerCreate */ "./dist/npm/models/transactions/offerCreate.js");
Object.defineProperty(exports, "OfferCreateFlags", ({ enumerable: true, get: function () { return offerCreate_1.OfferCreateFlags; } }));
var payment_1 = __webpack_require__(/*! ./payment */ "./dist/npm/models/transactions/payment.js");
Object.defineProperty(exports, "PaymentFlags", ({ enumerable: true, get: function () { return payment_1.PaymentFlags; } }));
var paymentChannelClaim_1 = __webpack_require__(/*! ./paymentChannelClaim */ "./dist/npm/models/transactions/paymentChannelClaim.js");
Object.defineProperty(exports, "PaymentChannelClaimFlags", ({ enumerable: true, get: function () { return paymentChannelClaim_1.PaymentChannelClaimFlags; } }));
var trustSet_1 = __webpack_require__(/*! ./trustSet */ "./dist/npm/models/transactions/trustSet.js");
Object.defineProperty(exports, "TrustSetFlags", ({ enumerable: true, get: function () { return trustSet_1.TrustSetFlags; } }));
var XChainModifyBridge_1 = __webpack_require__(/*! ./XChainModifyBridge */ "./dist/npm/models/transactions/XChainModifyBridge.js");
Object.defineProperty(exports, "XChainModifyBridgeFlags", ({ enumerable: true, get: function () { return XChainModifyBridge_1.XChainModifyBridgeFlags; } }));


/***/ }),

/***/ "./dist/npm/models/transactions/metadata.js":
/*!**************************************************!*\
  !*** ./dist/npm/models/transactions/metadata.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.isDeletedNode = exports.isModifiedNode = exports.isCreatedNode = void 0;
function isCreatedNode(node) {
    return Object.prototype.hasOwnProperty.call(node, `CreatedNode`);
}
exports.isCreatedNode = isCreatedNode;
function isModifiedNode(node) {
    return Object.prototype.hasOwnProperty.call(node, `ModifiedNode`);
}
exports.isModifiedNode = isModifiedNode;
function isDeletedNode(node) {
    return Object.prototype.hasOwnProperty.call(node, `DeletedNode`);
}
exports.isDeletedNode = isDeletedNode;


/***/ }),

/***/ "./dist/npm/models/transactions/offerCancel.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/offerCancel.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateOfferCancel = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateOfferCancel(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.OfferSequence === undefined) {
        throw new errors_1.ValidationError('OfferCancel: missing field OfferSequence');
    }
    if (typeof tx.OfferSequence !== 'number') {
        throw new errors_1.ValidationError('OfferCancel: OfferSequence must be a number');
    }
}
exports.validateOfferCancel = validateOfferCancel;


/***/ }),

/***/ "./dist/npm/models/transactions/offerCreate.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/offerCreate.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateOfferCreate = exports.OfferCreateFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var OfferCreateFlags;
(function (OfferCreateFlags) {
    OfferCreateFlags[OfferCreateFlags["tfPassive"] = 65536] = "tfPassive";
    OfferCreateFlags[OfferCreateFlags["tfImmediateOrCancel"] = 131072] = "tfImmediateOrCancel";
    OfferCreateFlags[OfferCreateFlags["tfFillOrKill"] = 262144] = "tfFillOrKill";
    OfferCreateFlags[OfferCreateFlags["tfSell"] = 524288] = "tfSell";
})(OfferCreateFlags || (exports.OfferCreateFlags = OfferCreateFlags = {}));
function validateOfferCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.TakerGets === undefined) {
        throw new errors_1.ValidationError('OfferCreate: missing field TakerGets');
    }
    if (tx.TakerPays === undefined) {
        throw new errors_1.ValidationError('OfferCreate: missing field TakerPays');
    }
    if (typeof tx.TakerGets !== 'string' && !(0, common_1.isAmount)(tx.TakerGets)) {
        throw new errors_1.ValidationError('OfferCreate: invalid TakerGets');
    }
    if (typeof tx.TakerPays !== 'string' && !(0, common_1.isAmount)(tx.TakerPays)) {
        throw new errors_1.ValidationError('OfferCreate: invalid TakerPays');
    }
    if (tx.Expiration !== undefined && typeof tx.Expiration !== 'number') {
        throw new errors_1.ValidationError('OfferCreate: invalid Expiration');
    }
    if (tx.OfferSequence !== undefined && typeof tx.OfferSequence !== 'number') {
        throw new errors_1.ValidationError('OfferCreate: invalid OfferSequence');
    }
}
exports.validateOfferCreate = validateOfferCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/oracleDelete.js":
/*!******************************************************!*\
  !*** ./dist/npm/models/transactions/oracleDelete.js ***!
  \******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateOracleDelete = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateOracleDelete(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'OracleDocumentID', common_1.isNumber);
}
exports.validateOracleDelete = validateOracleDelete;


/***/ }),

/***/ "./dist/npm/models/transactions/oracleSet.js":
/*!***************************************************!*\
  !*** ./dist/npm/models/transactions/oracleSet.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateOracleSet = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const PRICE_DATA_SERIES_MAX_LENGTH = 10;
const SCALE_MAX = 10;
const MINIMUM_ASSET_PRICE_LENGTH = 1;
const MAXIMUM_ASSET_PRICE_LENGTH = 16;
function validateOracleSet(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'OracleDocumentID', common_1.isNumber);
    (0, common_1.validateRequiredField)(tx, 'LastUpdateTime', common_1.isNumber);
    (0, common_1.validateOptionalField)(tx, 'Provider', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'URI', common_1.isString);
    (0, common_1.validateOptionalField)(tx, 'AssetClass', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'PriceDataSeries', (value) => {
        if (!Array.isArray(value)) {
            throw new errors_1.ValidationError('OracleSet: PriceDataSeries must be an array');
        }
        if (value.length > PRICE_DATA_SERIES_MAX_LENGTH) {
            throw new errors_1.ValidationError(`OracleSet: PriceDataSeries must have at most ${PRICE_DATA_SERIES_MAX_LENGTH} PriceData objects`);
        }
        for (const priceData of value) {
            if (typeof priceData !== 'object') {
                throw new errors_1.ValidationError('OracleSet: PriceDataSeries must be an array of objects');
            }
            if (priceData.PriceData == null) {
                throw new errors_1.ValidationError('OracleSet: PriceDataSeries must have a `PriceData` object');
            }
            if (Object.keys(priceData).length !== 1) {
                throw new errors_1.ValidationError('OracleSet: PriceDataSeries must only have a single PriceData object');
            }
            if (typeof priceData.PriceData.BaseAsset !== 'string') {
                throw new errors_1.ValidationError('OracleSet: PriceDataSeries must have a `BaseAsset` string');
            }
            if (typeof priceData.PriceData.QuoteAsset !== 'string') {
                throw new errors_1.ValidationError('OracleSet: PriceDataSeries must have a `QuoteAsset` string');
            }
            if ((priceData.PriceData.AssetPrice == null) !==
                (priceData.PriceData.Scale == null)) {
                throw new errors_1.ValidationError('OracleSet: PriceDataSeries must have both `AssetPrice` and `Scale` if any are present');
            }
            if ('AssetPrice' in priceData.PriceData) {
                if (!(0, common_1.isNumber)(priceData.PriceData.AssetPrice)) {
                    if (typeof priceData.PriceData.AssetPrice !== 'string') {
                        throw new errors_1.ValidationError('OracleSet: Field AssetPrice must be a string or a number');
                    }
                    if (!(0, utils_1.isHex)(priceData.PriceData.AssetPrice)) {
                        throw new errors_1.ValidationError('OracleSet: Field AssetPrice must be a valid hex string');
                    }
                    if (priceData.PriceData.AssetPrice.length <
                        MINIMUM_ASSET_PRICE_LENGTH ||
                        priceData.PriceData.AssetPrice.length > MAXIMUM_ASSET_PRICE_LENGTH) {
                        throw new errors_1.ValidationError(`OracleSet: Length of AssetPrice field must be between ${MINIMUM_ASSET_PRICE_LENGTH} and ${MAXIMUM_ASSET_PRICE_LENGTH} characters long`);
                    }
                }
            }
            if ('Scale' in priceData.PriceData &&
                !(0, common_1.isNumber)(priceData.PriceData.Scale)) {
                throw new errors_1.ValidationError('OracleSet: invalid field Scale');
            }
            if (priceData.PriceData.Scale < 0 ||
                priceData.PriceData.Scale > SCALE_MAX) {
                throw new errors_1.ValidationError(`OracleSet: Scale must be in range 0-${SCALE_MAX}`);
            }
        }
        return true;
    });
}
exports.validateOracleSet = validateOracleSet;


/***/ }),

/***/ "./dist/npm/models/transactions/payment.js":
/*!*************************************************!*\
  !*** ./dist/npm/models/transactions/payment.js ***!
  \*************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validatePayment = exports.PaymentFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var PaymentFlags;
(function (PaymentFlags) {
    PaymentFlags[PaymentFlags["tfNoRippleDirect"] = 65536] = "tfNoRippleDirect";
    PaymentFlags[PaymentFlags["tfPartialPayment"] = 131072] = "tfPartialPayment";
    PaymentFlags[PaymentFlags["tfLimitQuality"] = 262144] = "tfLimitQuality";
})(PaymentFlags || (exports.PaymentFlags = PaymentFlags = {}));
function validatePayment(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Amount === undefined) {
        throw new errors_1.ValidationError('PaymentTransaction: missing field Amount');
    }
    if (!(0, common_1.isAmount)(tx.Amount)) {
        throw new errors_1.ValidationError('PaymentTransaction: invalid Amount');
    }
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'DestinationTag', common_1.isNumber);
    (0, common_1.validateCredentialsList)(tx.CredentialIDs, tx.TransactionType, true, common_1.MAX_AUTHORIZED_CREDENTIALS);
    if (tx.InvoiceID !== undefined && typeof tx.InvoiceID !== 'string') {
        throw new errors_1.ValidationError('PaymentTransaction: InvoiceID must be a string');
    }
    if (tx.Paths !== undefined &&
        !isPaths(tx.Paths)) {
        throw new errors_1.ValidationError('PaymentTransaction: invalid Paths');
    }
    if (tx.SendMax !== undefined && !(0, common_1.isAmount)(tx.SendMax)) {
        throw new errors_1.ValidationError('PaymentTransaction: invalid SendMax');
    }
    checkPartialPayment(tx);
}
exports.validatePayment = validatePayment;
function checkPartialPayment(tx) {
    var _a;
    if (tx.DeliverMin != null) {
        if (tx.Flags == null) {
            throw new errors_1.ValidationError('PaymentTransaction: tfPartialPayment flag required with DeliverMin');
        }
        const flags = tx.Flags;
        const isTfPartialPayment = typeof flags === 'number'
            ? (0, utils_1.isFlagEnabled)(flags, PaymentFlags.tfPartialPayment)
            : (_a = flags.tfPartialPayment) !== null && _a !== void 0 ? _a : false;
        if (!isTfPartialPayment) {
            throw new errors_1.ValidationError('PaymentTransaction: tfPartialPayment flag required with DeliverMin');
        }
        if (!(0, common_1.isAmount)(tx.DeliverMin)) {
            throw new errors_1.ValidationError('PaymentTransaction: invalid DeliverMin');
        }
    }
}
function isPathStep(pathStep) {
    if (pathStep.account !== undefined && typeof pathStep.account !== 'string') {
        return false;
    }
    if (pathStep.currency !== undefined &&
        typeof pathStep.currency !== 'string') {
        return false;
    }
    if (pathStep.issuer !== undefined && typeof pathStep.issuer !== 'string') {
        return false;
    }
    if (pathStep.account !== undefined &&
        pathStep.currency === undefined &&
        pathStep.issuer === undefined) {
        return true;
    }
    if (pathStep.currency !== undefined || pathStep.issuer !== undefined) {
        return true;
    }
    return false;
}
function isPath(path) {
    for (const pathStep of path) {
        if (!isPathStep(pathStep)) {
            return false;
        }
    }
    return true;
}
function isPaths(paths) {
    if (!Array.isArray(paths) || paths.length === 0) {
        return false;
    }
    for (const path of paths) {
        if (!Array.isArray(path) || path.length === 0) {
            return false;
        }
        if (!isPath(path)) {
            return false;
        }
    }
    return true;
}


/***/ }),

/***/ "./dist/npm/models/transactions/paymentChannelClaim.js":
/*!*************************************************************!*\
  !*** ./dist/npm/models/transactions/paymentChannelClaim.js ***!
  \*************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validatePaymentChannelClaim = exports.PaymentChannelClaimFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var PaymentChannelClaimFlags;
(function (PaymentChannelClaimFlags) {
    PaymentChannelClaimFlags[PaymentChannelClaimFlags["tfRenew"] = 65536] = "tfRenew";
    PaymentChannelClaimFlags[PaymentChannelClaimFlags["tfClose"] = 131072] = "tfClose";
})(PaymentChannelClaimFlags || (exports.PaymentChannelClaimFlags = PaymentChannelClaimFlags = {}));
function validatePaymentChannelClaim(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateCredentialsList)(tx.CredentialIDs, tx.TransactionType, true, common_1.MAX_AUTHORIZED_CREDENTIALS);
    if (tx.Channel === undefined) {
        throw new errors_1.ValidationError('PaymentChannelClaim: missing Channel');
    }
    if (typeof tx.Channel !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelClaim: Channel must be a string');
    }
    if (tx.Balance !== undefined && typeof tx.Balance !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelClaim: Balance must be a string');
    }
    if (tx.Amount !== undefined && typeof tx.Amount !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelClaim: Amount must be a string');
    }
    if (tx.Signature !== undefined && typeof tx.Signature !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelClaim: Signature must be a string');
    }
    if (tx.PublicKey !== undefined && typeof tx.PublicKey !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelClaim: PublicKey must be a string');
    }
}
exports.validatePaymentChannelClaim = validatePaymentChannelClaim;


/***/ }),

/***/ "./dist/npm/models/transactions/paymentChannelCreate.js":
/*!**************************************************************!*\
  !*** ./dist/npm/models/transactions/paymentChannelCreate.js ***!
  \**************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validatePaymentChannelCreate = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validatePaymentChannelCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Amount === undefined) {
        throw new errors_1.ValidationError('PaymentChannelCreate: missing Amount');
    }
    if (typeof tx.Amount !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelCreate: Amount must be a string');
    }
    (0, common_1.validateRequiredField)(tx, 'Destination', common_1.isAccount);
    (0, common_1.validateOptionalField)(tx, 'DestinationTag', common_1.isNumber);
    if (tx.SettleDelay === undefined) {
        throw new errors_1.ValidationError('PaymentChannelCreate: missing SettleDelay');
    }
    if (typeof tx.SettleDelay !== 'number') {
        throw new errors_1.ValidationError('PaymentChannelCreate: SettleDelay must be a number');
    }
    if (tx.PublicKey === undefined) {
        throw new errors_1.ValidationError('PaymentChannelCreate: missing PublicKey');
    }
    if (typeof tx.PublicKey !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelCreate: PublicKey must be a string');
    }
    if (tx.CancelAfter !== undefined && typeof tx.CancelAfter !== 'number') {
        throw new errors_1.ValidationError('PaymentChannelCreate: CancelAfter must be a number');
    }
}
exports.validatePaymentChannelCreate = validatePaymentChannelCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/paymentChannelFund.js":
/*!************************************************************!*\
  !*** ./dist/npm/models/transactions/paymentChannelFund.js ***!
  \************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validatePaymentChannelFund = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validatePaymentChannelFund(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.Channel === undefined) {
        throw new errors_1.ValidationError('PaymentChannelFund: missing Channel');
    }
    if (typeof tx.Channel !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelFund: Channel must be a string');
    }
    if (tx.Amount === undefined) {
        throw new errors_1.ValidationError('PaymentChannelFund: missing Amount');
    }
    if (typeof tx.Amount !== 'string') {
        throw new errors_1.ValidationError('PaymentChannelFund: Amount must be a string');
    }
    if (tx.Expiration !== undefined && typeof tx.Expiration !== 'number') {
        throw new errors_1.ValidationError('PaymentChannelFund: Expiration must be a number');
    }
}
exports.validatePaymentChannelFund = validatePaymentChannelFund;


/***/ }),

/***/ "./dist/npm/models/transactions/permissionedDomainDelete.js":
/*!******************************************************************!*\
  !*** ./dist/npm/models/transactions/permissionedDomainDelete.js ***!
  \******************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validatePermissionedDomainDelete = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validatePermissionedDomainDelete(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateRequiredField)(tx, 'DomainID', common_1.isString);
}
exports.validatePermissionedDomainDelete = validatePermissionedDomainDelete;


/***/ }),

/***/ "./dist/npm/models/transactions/permissionedDomainSet.js":
/*!***************************************************************!*\
  !*** ./dist/npm/models/transactions/permissionedDomainSet.js ***!
  \***************************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validatePermissionedDomainSet = void 0;
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const MAX_ACCEPTED_CREDENTIALS = 10;
function validatePermissionedDomainSet(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    (0, common_1.validateOptionalField)(tx, 'DomainID', common_1.isString);
    (0, common_1.validateRequiredField)(tx, 'AcceptedCredentials', () => tx.AcceptedCredentials instanceof Array);
    (0, common_1.validateCredentialsList)(tx.AcceptedCredentials, tx.TransactionType, false, MAX_ACCEPTED_CREDENTIALS);
}
exports.validatePermissionedDomainSet = validatePermissionedDomainSet;


/***/ }),

/***/ "./dist/npm/models/transactions/setRegularKey.js":
/*!*******************************************************!*\
  !*** ./dist/npm/models/transactions/setRegularKey.js ***!
  \*******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateSetRegularKey = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
function validateSetRegularKey(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.RegularKey !== undefined && typeof tx.RegularKey !== 'string') {
        throw new errors_1.ValidationError('SetRegularKey: RegularKey must be a string');
    }
}
exports.validateSetRegularKey = validateSetRegularKey;


/***/ }),

/***/ "./dist/npm/models/transactions/signerListSet.js":
/*!*******************************************************!*\
  !*** ./dist/npm/models/transactions/signerListSet.js ***!
  \*******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateSignerListSet = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const MAX_SIGNERS = 32;
const HEX_WALLET_LOCATOR_REGEX = /^[0-9A-Fa-f]{64}$/u;
function validateSignerListSet(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    if (tx.SignerQuorum === undefined) {
        throw new errors_1.ValidationError('SignerListSet: missing field SignerQuorum');
    }
    if (typeof tx.SignerQuorum !== 'number') {
        throw new errors_1.ValidationError('SignerListSet: invalid SignerQuorum');
    }
    if (tx.SignerQuorum === 0) {
        return;
    }
    if (tx.SignerEntries === undefined) {
        throw new errors_1.ValidationError('SignerListSet: missing field SignerEntries');
    }
    if (!Array.isArray(tx.SignerEntries)) {
        throw new errors_1.ValidationError('SignerListSet: invalid SignerEntries');
    }
    if (tx.SignerEntries.length === 0) {
        throw new errors_1.ValidationError('SignerListSet: need at least 1 member in SignerEntries');
    }
    if (tx.SignerEntries.length > MAX_SIGNERS) {
        throw new errors_1.ValidationError(`SignerListSet: maximum of ${MAX_SIGNERS} members allowed in SignerEntries`);
    }
    for (const entry of tx.SignerEntries) {
        const signerEntry = entry;
        const { WalletLocator } = signerEntry.SignerEntry;
        if (WalletLocator !== undefined &&
            !HEX_WALLET_LOCATOR_REGEX.test(WalletLocator)) {
            throw new errors_1.ValidationError(`SignerListSet: WalletLocator in SignerEntry must be a 256-bit (32-byte) hexadecimal value`);
        }
    }
}
exports.validateSignerListSet = validateSignerListSet;


/***/ }),

/***/ "./dist/npm/models/transactions/ticketCreate.js":
/*!******************************************************!*\
  !*** ./dist/npm/models/transactions/ticketCreate.js ***!
  \******************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateTicketCreate = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const MAX_TICKETS = 250;
function validateTicketCreate(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    const { TicketCount } = tx;
    if (TicketCount === undefined) {
        throw new errors_1.ValidationError('TicketCreate: missing field TicketCount');
    }
    if (typeof TicketCount !== 'number') {
        throw new errors_1.ValidationError('TicketCreate: TicketCount must be a number');
    }
    if (!Number.isInteger(TicketCount) ||
        TicketCount < 1 ||
        TicketCount > MAX_TICKETS) {
        throw new errors_1.ValidationError('TicketCreate: TicketCount must be an integer from 1 to 250');
    }
}
exports.validateTicketCreate = validateTicketCreate;


/***/ }),

/***/ "./dist/npm/models/transactions/transaction.js":
/*!*****************************************************!*\
  !*** ./dist/npm/models/transactions/transaction.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validate = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/models/utils/index.js");
const flags_1 = __webpack_require__(/*! ../utils/flags */ "./dist/npm/models/utils/flags.js");
const accountDelete_1 = __webpack_require__(/*! ./accountDelete */ "./dist/npm/models/transactions/accountDelete.js");
const accountSet_1 = __webpack_require__(/*! ./accountSet */ "./dist/npm/models/transactions/accountSet.js");
const AMMBid_1 = __webpack_require__(/*! ./AMMBid */ "./dist/npm/models/transactions/AMMBid.js");
const AMMClawback_1 = __webpack_require__(/*! ./AMMClawback */ "./dist/npm/models/transactions/AMMClawback.js");
const AMMCreate_1 = __webpack_require__(/*! ./AMMCreate */ "./dist/npm/models/transactions/AMMCreate.js");
const AMMDelete_1 = __webpack_require__(/*! ./AMMDelete */ "./dist/npm/models/transactions/AMMDelete.js");
const AMMDeposit_1 = __webpack_require__(/*! ./AMMDeposit */ "./dist/npm/models/transactions/AMMDeposit.js");
const AMMVote_1 = __webpack_require__(/*! ./AMMVote */ "./dist/npm/models/transactions/AMMVote.js");
const AMMWithdraw_1 = __webpack_require__(/*! ./AMMWithdraw */ "./dist/npm/models/transactions/AMMWithdraw.js");
const checkCancel_1 = __webpack_require__(/*! ./checkCancel */ "./dist/npm/models/transactions/checkCancel.js");
const checkCash_1 = __webpack_require__(/*! ./checkCash */ "./dist/npm/models/transactions/checkCash.js");
const checkCreate_1 = __webpack_require__(/*! ./checkCreate */ "./dist/npm/models/transactions/checkCreate.js");
const clawback_1 = __webpack_require__(/*! ./clawback */ "./dist/npm/models/transactions/clawback.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
const CredentialAccept_1 = __webpack_require__(/*! ./CredentialAccept */ "./dist/npm/models/transactions/CredentialAccept.js");
const CredentialCreate_1 = __webpack_require__(/*! ./CredentialCreate */ "./dist/npm/models/transactions/CredentialCreate.js");
const CredentialDelete_1 = __webpack_require__(/*! ./CredentialDelete */ "./dist/npm/models/transactions/CredentialDelete.js");
const depositPreauth_1 = __webpack_require__(/*! ./depositPreauth */ "./dist/npm/models/transactions/depositPreauth.js");
const DIDDelete_1 = __webpack_require__(/*! ./DIDDelete */ "./dist/npm/models/transactions/DIDDelete.js");
const DIDSet_1 = __webpack_require__(/*! ./DIDSet */ "./dist/npm/models/transactions/DIDSet.js");
const escrowCancel_1 = __webpack_require__(/*! ./escrowCancel */ "./dist/npm/models/transactions/escrowCancel.js");
const escrowCreate_1 = __webpack_require__(/*! ./escrowCreate */ "./dist/npm/models/transactions/escrowCreate.js");
const escrowFinish_1 = __webpack_require__(/*! ./escrowFinish */ "./dist/npm/models/transactions/escrowFinish.js");
const MPTokenAuthorize_1 = __webpack_require__(/*! ./MPTokenAuthorize */ "./dist/npm/models/transactions/MPTokenAuthorize.js");
const MPTokenIssuanceCreate_1 = __webpack_require__(/*! ./MPTokenIssuanceCreate */ "./dist/npm/models/transactions/MPTokenIssuanceCreate.js");
const MPTokenIssuanceDestroy_1 = __webpack_require__(/*! ./MPTokenIssuanceDestroy */ "./dist/npm/models/transactions/MPTokenIssuanceDestroy.js");
const MPTokenIssuanceSet_1 = __webpack_require__(/*! ./MPTokenIssuanceSet */ "./dist/npm/models/transactions/MPTokenIssuanceSet.js");
const NFTokenAcceptOffer_1 = __webpack_require__(/*! ./NFTokenAcceptOffer */ "./dist/npm/models/transactions/NFTokenAcceptOffer.js");
const NFTokenBurn_1 = __webpack_require__(/*! ./NFTokenBurn */ "./dist/npm/models/transactions/NFTokenBurn.js");
const NFTokenCancelOffer_1 = __webpack_require__(/*! ./NFTokenCancelOffer */ "./dist/npm/models/transactions/NFTokenCancelOffer.js");
const NFTokenCreateOffer_1 = __webpack_require__(/*! ./NFTokenCreateOffer */ "./dist/npm/models/transactions/NFTokenCreateOffer.js");
const NFTokenMint_1 = __webpack_require__(/*! ./NFTokenMint */ "./dist/npm/models/transactions/NFTokenMint.js");
const NFTokenModify_1 = __webpack_require__(/*! ./NFTokenModify */ "./dist/npm/models/transactions/NFTokenModify.js");
const offerCancel_1 = __webpack_require__(/*! ./offerCancel */ "./dist/npm/models/transactions/offerCancel.js");
const offerCreate_1 = __webpack_require__(/*! ./offerCreate */ "./dist/npm/models/transactions/offerCreate.js");
const oracleDelete_1 = __webpack_require__(/*! ./oracleDelete */ "./dist/npm/models/transactions/oracleDelete.js");
const oracleSet_1 = __webpack_require__(/*! ./oracleSet */ "./dist/npm/models/transactions/oracleSet.js");
const payment_1 = __webpack_require__(/*! ./payment */ "./dist/npm/models/transactions/payment.js");
const paymentChannelClaim_1 = __webpack_require__(/*! ./paymentChannelClaim */ "./dist/npm/models/transactions/paymentChannelClaim.js");
const paymentChannelCreate_1 = __webpack_require__(/*! ./paymentChannelCreate */ "./dist/npm/models/transactions/paymentChannelCreate.js");
const paymentChannelFund_1 = __webpack_require__(/*! ./paymentChannelFund */ "./dist/npm/models/transactions/paymentChannelFund.js");
const permissionedDomainDelete_1 = __webpack_require__(/*! ./permissionedDomainDelete */ "./dist/npm/models/transactions/permissionedDomainDelete.js");
const permissionedDomainSet_1 = __webpack_require__(/*! ./permissionedDomainSet */ "./dist/npm/models/transactions/permissionedDomainSet.js");
const setRegularKey_1 = __webpack_require__(/*! ./setRegularKey */ "./dist/npm/models/transactions/setRegularKey.js");
const signerListSet_1 = __webpack_require__(/*! ./signerListSet */ "./dist/npm/models/transactions/signerListSet.js");
const ticketCreate_1 = __webpack_require__(/*! ./ticketCreate */ "./dist/npm/models/transactions/ticketCreate.js");
const trustSet_1 = __webpack_require__(/*! ./trustSet */ "./dist/npm/models/transactions/trustSet.js");
const XChainAccountCreateCommit_1 = __webpack_require__(/*! ./XChainAccountCreateCommit */ "./dist/npm/models/transactions/XChainAccountCreateCommit.js");
const XChainAddAccountCreateAttestation_1 = __webpack_require__(/*! ./XChainAddAccountCreateAttestation */ "./dist/npm/models/transactions/XChainAddAccountCreateAttestation.js");
const XChainAddClaimAttestation_1 = __webpack_require__(/*! ./XChainAddClaimAttestation */ "./dist/npm/models/transactions/XChainAddClaimAttestation.js");
const XChainClaim_1 = __webpack_require__(/*! ./XChainClaim */ "./dist/npm/models/transactions/XChainClaim.js");
const XChainCommit_1 = __webpack_require__(/*! ./XChainCommit */ "./dist/npm/models/transactions/XChainCommit.js");
const XChainCreateBridge_1 = __webpack_require__(/*! ./XChainCreateBridge */ "./dist/npm/models/transactions/XChainCreateBridge.js");
const XChainCreateClaimID_1 = __webpack_require__(/*! ./XChainCreateClaimID */ "./dist/npm/models/transactions/XChainCreateClaimID.js");
const XChainModifyBridge_1 = __webpack_require__(/*! ./XChainModifyBridge */ "./dist/npm/models/transactions/XChainModifyBridge.js");
function validate(transaction) {
    const tx = Object.assign({}, transaction);
    if (tx.TransactionType == null) {
        throw new errors_1.ValidationError('Object does not have a `TransactionType`');
    }
    if (typeof tx.TransactionType !== 'string') {
        throw new errors_1.ValidationError("Object's `TransactionType` is not a string");
    }
    if (tx.Memos != null && typeof tx.Memos !== 'object') {
        throw new errors_1.ValidationError('Memo must be array');
    }
    if (tx.Memos != null) {
        ;
        tx.Memos.forEach((memo) => {
            if ((memo === null || memo === void 0 ? void 0 : memo.Memo) == null) {
                throw new errors_1.ValidationError('Memo data must be in a `Memo` field');
            }
            if (memo.Memo.MemoData) {
                if (!(0, utils_1.isHex)(memo.Memo.MemoData)) {
                    throw new errors_1.ValidationError('MemoData field must be a hex value');
                }
            }
            if (memo.Memo.MemoType) {
                if (!(0, utils_1.isHex)(memo.Memo.MemoType)) {
                    throw new errors_1.ValidationError('MemoType field must be a hex value');
                }
            }
            if (memo.Memo.MemoFormat) {
                if (!(0, utils_1.isHex)(memo.Memo.MemoFormat)) {
                    throw new errors_1.ValidationError('MemoFormat field must be a hex value');
                }
            }
        });
    }
    Object.keys(tx).forEach((key) => {
        const standard_currency_code_len = 3;
        if (tx[key] && (0, common_1.isIssuedCurrency)(tx[key])) {
            const txCurrency = tx[key].currency;
            if (txCurrency.length === standard_currency_code_len &&
                txCurrency.toUpperCase() === 'XRP') {
                throw new errors_1.ValidationError(`Cannot have an issued currency with a similar standard code to XRP (received '${txCurrency}'). XRP is not an issued currency.`);
            }
        }
    });
    tx.Flags = (0, flags_1.convertTxFlagsToNumber)(tx);
    switch (tx.TransactionType) {
        case 'AMMBid':
            (0, AMMBid_1.validateAMMBid)(tx);
            break;
        case 'AMMClawback':
            (0, AMMClawback_1.validateAMMClawback)(tx);
            break;
        case 'AMMCreate':
            (0, AMMCreate_1.validateAMMCreate)(tx);
            break;
        case 'AMMDelete':
            (0, AMMDelete_1.validateAMMDelete)(tx);
            break;
        case 'AMMDeposit':
            (0, AMMDeposit_1.validateAMMDeposit)(tx);
            break;
        case 'AMMVote':
            (0, AMMVote_1.validateAMMVote)(tx);
            break;
        case 'AMMWithdraw':
            (0, AMMWithdraw_1.validateAMMWithdraw)(tx);
            break;
        case 'AccountDelete':
            (0, accountDelete_1.validateAccountDelete)(tx);
            break;
        case 'AccountSet':
            (0, accountSet_1.validateAccountSet)(tx);
            break;
        case 'CheckCancel':
            (0, checkCancel_1.validateCheckCancel)(tx);
            break;
        case 'CheckCash':
            (0, checkCash_1.validateCheckCash)(tx);
            break;
        case 'CheckCreate':
            (0, checkCreate_1.validateCheckCreate)(tx);
            break;
        case 'Clawback':
            (0, clawback_1.validateClawback)(tx);
            break;
        case 'CredentialAccept':
            (0, CredentialAccept_1.validateCredentialAccept)(tx);
            break;
        case 'CredentialCreate':
            (0, CredentialCreate_1.validateCredentialCreate)(tx);
            break;
        case 'CredentialDelete':
            (0, CredentialDelete_1.validateCredentialDelete)(tx);
            break;
        case 'DIDDelete':
            (0, DIDDelete_1.validateDIDDelete)(tx);
            break;
        case 'DIDSet':
            (0, DIDSet_1.validateDIDSet)(tx);
            break;
        case 'DepositPreauth':
            (0, depositPreauth_1.validateDepositPreauth)(tx);
            break;
        case 'EscrowCancel':
            (0, escrowCancel_1.validateEscrowCancel)(tx);
            break;
        case 'EscrowCreate':
            (0, escrowCreate_1.validateEscrowCreate)(tx);
            break;
        case 'EscrowFinish':
            (0, escrowFinish_1.validateEscrowFinish)(tx);
            break;
        case 'MPTokenAuthorize':
            (0, MPTokenAuthorize_1.validateMPTokenAuthorize)(tx);
            break;
        case 'MPTokenIssuanceCreate':
            (0, MPTokenIssuanceCreate_1.validateMPTokenIssuanceCreate)(tx);
            break;
        case 'MPTokenIssuanceDestroy':
            (0, MPTokenIssuanceDestroy_1.validateMPTokenIssuanceDestroy)(tx);
            break;
        case 'MPTokenIssuanceSet':
            (0, MPTokenIssuanceSet_1.validateMPTokenIssuanceSet)(tx);
            break;
        case 'NFTokenAcceptOffer':
            (0, NFTokenAcceptOffer_1.validateNFTokenAcceptOffer)(tx);
            break;
        case 'NFTokenBurn':
            (0, NFTokenBurn_1.validateNFTokenBurn)(tx);
            break;
        case 'NFTokenCancelOffer':
            (0, NFTokenCancelOffer_1.validateNFTokenCancelOffer)(tx);
            break;
        case 'NFTokenCreateOffer':
            (0, NFTokenCreateOffer_1.validateNFTokenCreateOffer)(tx);
            break;
        case 'NFTokenMint':
            (0, NFTokenMint_1.validateNFTokenMint)(tx);
            break;
        case 'NFTokenModify':
            (0, NFTokenModify_1.validateNFTokenModify)(tx);
            break;
        case 'OfferCancel':
            (0, offerCancel_1.validateOfferCancel)(tx);
            break;
        case 'OfferCreate':
            (0, offerCreate_1.validateOfferCreate)(tx);
            break;
        case 'OracleDelete':
            (0, oracleDelete_1.validateOracleDelete)(tx);
            break;
        case 'OracleSet':
            (0, oracleSet_1.validateOracleSet)(tx);
            break;
        case 'Payment':
            (0, payment_1.validatePayment)(tx);
            break;
        case 'PaymentChannelClaim':
            (0, paymentChannelClaim_1.validatePaymentChannelClaim)(tx);
            break;
        case 'PaymentChannelCreate':
            (0, paymentChannelCreate_1.validatePaymentChannelCreate)(tx);
            break;
        case 'PaymentChannelFund':
            (0, paymentChannelFund_1.validatePaymentChannelFund)(tx);
            break;
        case 'PermissionedDomainSet':
            (0, permissionedDomainSet_1.validatePermissionedDomainSet)(tx);
            break;
        case 'PermissionedDomainDelete':
            (0, permissionedDomainDelete_1.validatePermissionedDomainDelete)(tx);
            break;
        case 'SetRegularKey':
            (0, setRegularKey_1.validateSetRegularKey)(tx);
            break;
        case 'SignerListSet':
            (0, signerListSet_1.validateSignerListSet)(tx);
            break;
        case 'TicketCreate':
            (0, ticketCreate_1.validateTicketCreate)(tx);
            break;
        case 'TrustSet':
            (0, trustSet_1.validateTrustSet)(tx);
            break;
        case 'XChainAccountCreateCommit':
            (0, XChainAccountCreateCommit_1.validateXChainAccountCreateCommit)(tx);
            break;
        case 'XChainAddAccountCreateAttestation':
            (0, XChainAddAccountCreateAttestation_1.validateXChainAddAccountCreateAttestation)(tx);
            break;
        case 'XChainAddClaimAttestation':
            (0, XChainAddClaimAttestation_1.validateXChainAddClaimAttestation)(tx);
            break;
        case 'XChainClaim':
            (0, XChainClaim_1.validateXChainClaim)(tx);
            break;
        case 'XChainCommit':
            (0, XChainCommit_1.validateXChainCommit)(tx);
            break;
        case 'XChainCreateBridge':
            (0, XChainCreateBridge_1.validateXChainCreateBridge)(tx);
            break;
        case 'XChainCreateClaimID':
            (0, XChainCreateClaimID_1.validateXChainCreateClaimID)(tx);
            break;
        case 'XChainModifyBridge':
            (0, XChainModifyBridge_1.validateXChainModifyBridge)(tx);
            break;
        default:
            throw new errors_1.ValidationError(`Invalid field TransactionType: ${tx.TransactionType}`);
    }
}
exports.validate = validate;


/***/ }),

/***/ "./dist/npm/models/transactions/trustSet.js":
/*!**************************************************!*\
  !*** ./dist/npm/models/transactions/trustSet.js ***!
  \**************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.validateTrustSet = exports.TrustSetFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const common_1 = __webpack_require__(/*! ./common */ "./dist/npm/models/transactions/common.js");
var TrustSetFlags;
(function (TrustSetFlags) {
    TrustSetFlags[TrustSetFlags["tfSetfAuth"] = 65536] = "tfSetfAuth";
    TrustSetFlags[TrustSetFlags["tfSetNoRipple"] = 131072] = "tfSetNoRipple";
    TrustSetFlags[TrustSetFlags["tfClearNoRipple"] = 262144] = "tfClearNoRipple";
    TrustSetFlags[TrustSetFlags["tfSetFreeze"] = 1048576] = "tfSetFreeze";
    TrustSetFlags[TrustSetFlags["tfClearFreeze"] = 2097152] = "tfClearFreeze";
    TrustSetFlags[TrustSetFlags["tfSetDeepFreeze"] = 4194304] = "tfSetDeepFreeze";
    TrustSetFlags[TrustSetFlags["tfClearDeepFreeze"] = 8388608] = "tfClearDeepFreeze";
})(TrustSetFlags || (exports.TrustSetFlags = TrustSetFlags = {}));
function validateTrustSet(tx) {
    (0, common_1.validateBaseTransaction)(tx);
    const { LimitAmount, QualityIn, QualityOut } = tx;
    if (LimitAmount === undefined) {
        throw new errors_1.ValidationError('TrustSet: missing field LimitAmount');
    }
    if (!(0, common_1.isAmount)(LimitAmount)) {
        throw new errors_1.ValidationError('TrustSet: invalid LimitAmount');
    }
    if (QualityIn !== undefined && typeof QualityIn !== 'number') {
        throw new errors_1.ValidationError('TrustSet: QualityIn must be a number');
    }
    if (QualityOut !== undefined && typeof QualityOut !== 'number') {
        throw new errors_1.ValidationError('TrustSet: QualityOut must be a number');
    }
}
exports.validateTrustSet = validateTrustSet;


/***/ }),

/***/ "./dist/npm/models/utils/flags.js":
/*!****************************************!*\
  !*** ./dist/npm/models/utils/flags.js ***!
  \****************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.parseTransactionFlags = exports.convertTxFlagsToNumber = exports.setTransactionFlagsToNumber = exports.parseAccountRootFlags = void 0;
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const AccountRoot_1 = __webpack_require__(/*! ../ledger/AccountRoot */ "./dist/npm/models/ledger/AccountRoot.js");
const accountSet_1 = __webpack_require__(/*! ../transactions/accountSet */ "./dist/npm/models/transactions/accountSet.js");
const AMMClawback_1 = __webpack_require__(/*! ../transactions/AMMClawback */ "./dist/npm/models/transactions/AMMClawback.js");
const AMMDeposit_1 = __webpack_require__(/*! ../transactions/AMMDeposit */ "./dist/npm/models/transactions/AMMDeposit.js");
const AMMWithdraw_1 = __webpack_require__(/*! ../transactions/AMMWithdraw */ "./dist/npm/models/transactions/AMMWithdraw.js");
const MPTokenAuthorize_1 = __webpack_require__(/*! ../transactions/MPTokenAuthorize */ "./dist/npm/models/transactions/MPTokenAuthorize.js");
const MPTokenIssuanceCreate_1 = __webpack_require__(/*! ../transactions/MPTokenIssuanceCreate */ "./dist/npm/models/transactions/MPTokenIssuanceCreate.js");
const MPTokenIssuanceSet_1 = __webpack_require__(/*! ../transactions/MPTokenIssuanceSet */ "./dist/npm/models/transactions/MPTokenIssuanceSet.js");
const NFTokenCreateOffer_1 = __webpack_require__(/*! ../transactions/NFTokenCreateOffer */ "./dist/npm/models/transactions/NFTokenCreateOffer.js");
const NFTokenMint_1 = __webpack_require__(/*! ../transactions/NFTokenMint */ "./dist/npm/models/transactions/NFTokenMint.js");
const offerCreate_1 = __webpack_require__(/*! ../transactions/offerCreate */ "./dist/npm/models/transactions/offerCreate.js");
const payment_1 = __webpack_require__(/*! ../transactions/payment */ "./dist/npm/models/transactions/payment.js");
const paymentChannelClaim_1 = __webpack_require__(/*! ../transactions/paymentChannelClaim */ "./dist/npm/models/transactions/paymentChannelClaim.js");
const trustSet_1 = __webpack_require__(/*! ../transactions/trustSet */ "./dist/npm/models/transactions/trustSet.js");
const XChainModifyBridge_1 = __webpack_require__(/*! ../transactions/XChainModifyBridge */ "./dist/npm/models/transactions/XChainModifyBridge.js");
const _1 = __webpack_require__(/*! . */ "./dist/npm/models/utils/index.js");
function parseAccountRootFlags(flags) {
    const flagsInterface = {};
    Object.values(AccountRoot_1.AccountRootFlags).forEach((flag) => {
        if (typeof flag === 'string' &&
            (0, _1.isFlagEnabled)(flags, AccountRoot_1.AccountRootFlags[flag])) {
            flagsInterface[flag] = true;
        }
    });
    return flagsInterface;
}
exports.parseAccountRootFlags = parseAccountRootFlags;
const txToFlag = {
    AccountSet: accountSet_1.AccountSetTfFlags,
    AMMClawback: AMMClawback_1.AMMClawbackFlags,
    AMMDeposit: AMMDeposit_1.AMMDepositFlags,
    AMMWithdraw: AMMWithdraw_1.AMMWithdrawFlags,
    MPTokenAuthorize: MPTokenAuthorize_1.MPTokenAuthorizeFlags,
    MPTokenIssuanceCreate: MPTokenIssuanceCreate_1.MPTokenIssuanceCreateFlags,
    MPTokenIssuanceSet: MPTokenIssuanceSet_1.MPTokenIssuanceSetFlags,
    NFTokenCreateOffer: NFTokenCreateOffer_1.NFTokenCreateOfferFlags,
    NFTokenMint: NFTokenMint_1.NFTokenMintFlags,
    OfferCreate: offerCreate_1.OfferCreateFlags,
    PaymentChannelClaim: paymentChannelClaim_1.PaymentChannelClaimFlags,
    Payment: payment_1.PaymentFlags,
    TrustSet: trustSet_1.TrustSetFlags,
    XChainModifyBridge: XChainModifyBridge_1.XChainModifyBridgeFlags,
};
function isTxToFlagKey(transactionType) {
    return transactionType in txToFlag;
}
function setTransactionFlagsToNumber(tx) {
    console.warn('This function is deprecated. Use convertTxFlagsToNumber() instead and use the returned value to modify the Transaction.Flags from the caller.');
    if (tx.Flags) {
        tx.Flags = convertTxFlagsToNumber(tx);
    }
}
exports.setTransactionFlagsToNumber = setTransactionFlagsToNumber;
function convertTxFlagsToNumber(tx) {
    if (!tx.Flags) {
        return 0;
    }
    if (typeof tx.Flags === 'number') {
        return tx.Flags;
    }
    if (isTxToFlagKey(tx.TransactionType)) {
        const flagEnum = txToFlag[tx.TransactionType];
        return Object.keys(tx.Flags).reduce((resultFlags, flag) => {
            var _a;
            if (flagEnum[flag] == null) {
                throw new errors_1.ValidationError(`Invalid flag ${flag}. Valid flags are ${JSON.stringify(flagEnum)}`);
            }
            return ((_a = tx.Flags) === null || _a === void 0 ? void 0 : _a[flag]) ? resultFlags | flagEnum[flag] : resultFlags;
        }, 0);
    }
    return 0;
}
exports.convertTxFlagsToNumber = convertTxFlagsToNumber;
function parseTransactionFlags(tx) {
    const flags = convertTxFlagsToNumber(tx);
    if (flags === 0) {
        return {};
    }
    const booleanFlagMap = {};
    if (isTxToFlagKey(tx.TransactionType)) {
        const transactionTypeFlags = txToFlag[tx.TransactionType];
        Object.values(transactionTypeFlags).forEach((flag) => {
            if (typeof flag === 'string' &&
                (0, _1.isFlagEnabled)(flags, transactionTypeFlags[flag])) {
                booleanFlagMap[flag] = true;
            }
        });
    }
    return booleanFlagMap;
}
exports.parseTransactionFlags = parseTransactionFlags;


/***/ }),

/***/ "./dist/npm/models/utils/index.js":
/*!****************************************!*\
  !*** ./dist/npm/models/utils/index.js ***!
  \****************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.isHex = exports.isFlagEnabled = exports.onlyHasFields = exports.INTEGER_SANITY_CHECK = void 0;
const HEX_REGEX = /^[0-9A-Fa-f]+$/u;
exports.INTEGER_SANITY_CHECK = /^[0-9]+$/u;
function onlyHasFields(obj, fields) {
    return Object.keys(obj).every((key) => fields.includes(key));
}
exports.onlyHasFields = onlyHasFields;
function isFlagEnabled(Flags, checkFlag) {
    return (BigInt(checkFlag) & BigInt(Flags)) === BigInt(checkFlag);
}
exports.isFlagEnabled = isFlagEnabled;
function isHex(str) {
    return HEX_REGEX.test(str);
}
exports.isHex = isHex;


/***/ }),

/***/ "./dist/npm/sugar/autofill.js":
/*!************************************!*\
  !*** ./dist/npm/sugar/autofill.js ***!
  \************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.checkAccountDeleteBlockers = exports.setLatestValidatedLedgerSequence = exports.calculateFeePerTransactionType = exports.setNextValidSequenceNumber = exports.setValidAddresses = exports.txNeedsNetworkID = void 0;
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/utils/index.js");
const getFeeXrp_1 = __importDefault(__webpack_require__(/*! ./getFeeXrp */ "./dist/npm/sugar/getFeeXrp.js"));
const LEDGER_OFFSET = 20;
const RESTRICTED_NETWORKS = 1024;
const REQUIRED_NETWORKID_VERSION = '1.11.0';
function isNotLaterRippledVersion(source, target) {
    if (source === target) {
        return true;
    }
    const sourceDecomp = source.split('.');
    const targetDecomp = target.split('.');
    const sourceMajor = parseInt(sourceDecomp[0], 10);
    const sourceMinor = parseInt(sourceDecomp[1], 10);
    const targetMajor = parseInt(targetDecomp[0], 10);
    const targetMinor = parseInt(targetDecomp[1], 10);
    if (sourceMajor !== targetMajor) {
        return sourceMajor < targetMajor;
    }
    if (sourceMinor !== targetMinor) {
        return sourceMinor < targetMinor;
    }
    const sourcePatch = sourceDecomp[2].split('-');
    const targetPatch = targetDecomp[2].split('-');
    const sourcePatchVersion = parseInt(sourcePatch[0], 10);
    const targetPatchVersion = parseInt(targetPatch[0], 10);
    if (sourcePatchVersion !== targetPatchVersion) {
        return sourcePatchVersion < targetPatchVersion;
    }
    if (sourcePatch.length !== targetPatch.length) {
        return sourcePatch.length > targetPatch.length;
    }
    if (sourcePatch.length === 2) {
        if (!sourcePatch[1][0].startsWith(targetPatch[1][0])) {
            return sourcePatch[1] < targetPatch[1];
        }
        if (sourcePatch[1].startsWith('b')) {
            return (parseInt(sourcePatch[1].slice(1), 10) <
                parseInt(targetPatch[1].slice(1), 10));
        }
        return (parseInt(sourcePatch[1].slice(2), 10) <
            parseInt(targetPatch[1].slice(2), 10));
    }
    return false;
}
function txNeedsNetworkID(client) {
    if (client.networkID !== undefined &&
        client.networkID > RESTRICTED_NETWORKS) {
        if (client.buildVersion &&
            isNotLaterRippledVersion(REQUIRED_NETWORKID_VERSION, client.buildVersion)) {
            return true;
        }
    }
    return false;
}
exports.txNeedsNetworkID = txNeedsNetworkID;
function setValidAddresses(tx) {
    validateAccountAddress(tx, 'Account', 'SourceTag');
    if (tx['Destination'] != null) {
        validateAccountAddress(tx, 'Destination', 'DestinationTag');
    }
    convertToClassicAddress(tx, 'Authorize');
    convertToClassicAddress(tx, 'Unauthorize');
    convertToClassicAddress(tx, 'Owner');
    convertToClassicAddress(tx, 'RegularKey');
}
exports.setValidAddresses = setValidAddresses;
function validateAccountAddress(tx, accountField, tagField) {
    const { classicAccount, tag } = getClassicAccountAndTag(tx[accountField]);
    tx[accountField] = classicAccount;
    if (tag != null && tag !== false) {
        if (tx[tagField] && tx[tagField] !== tag) {
            throw new errors_1.ValidationError(`The ${tagField}, if present, must match the tag of the ${accountField} X-address`);
        }
        tx[tagField] = tag;
    }
}
function getClassicAccountAndTag(Account, expectedTag) {
    if ((0, ripple_address_codec_1.isValidXAddress)(Account)) {
        const classic = (0, ripple_address_codec_1.xAddressToClassicAddress)(Account);
        if (expectedTag != null && classic.tag !== expectedTag) {
            throw new errors_1.ValidationError('address includes a tag that does not match the tag specified in the transaction');
        }
        return {
            classicAccount: classic.classicAddress,
            tag: classic.tag,
        };
    }
    return {
        classicAccount: Account,
        tag: expectedTag,
    };
}
function convertToClassicAddress(tx, fieldName) {
    const account = tx[fieldName];
    if (typeof account === 'string') {
        const { classicAccount } = getClassicAccountAndTag(account);
        tx[fieldName] = classicAccount;
    }
}
function setNextValidSequenceNumber(client, tx) {
    return __awaiter(this, void 0, void 0, function* () {
        const request = {
            command: 'account_info',
            account: tx.Account,
            ledger_index: 'current',
        };
        const data = yield client.request(request);
        tx.Sequence = data.result.account_data.Sequence;
    });
}
exports.setNextValidSequenceNumber = setNextValidSequenceNumber;
function fetchOwnerReserveFee(client) {
    var _a;
    return __awaiter(this, void 0, void 0, function* () {
        const response = yield client.request({ command: 'server_state' });
        const fee = (_a = response.result.state.validated_ledger) === null || _a === void 0 ? void 0 : _a.reserve_inc;
        if (fee == null) {
            return Promise.reject(new Error('Could not fetch Owner Reserve.'));
        }
        return new bignumber_js_1.default(fee);
    });
}
function calculateFeePerTransactionType(client, tx, signersCount = 0) {
    return __awaiter(this, void 0, void 0, function* () {
        const netFeeXRP = yield (0, getFeeXrp_1.default)(client);
        const netFeeDrops = (0, utils_1.xrpToDrops)(netFeeXRP);
        let baseFee = new bignumber_js_1.default(netFeeDrops);
        if (tx.TransactionType === 'EscrowFinish' && tx.Fulfillment != null) {
            const fulfillmentBytesSize = Math.ceil(tx.Fulfillment.length / 2);
            baseFee = new bignumber_js_1.default(scaleValue(netFeeDrops, 33 + fulfillmentBytesSize / 16));
        }
        const isSpecialTxCost = ['AccountDelete', 'AMMCreate'].includes(tx.TransactionType);
        if (isSpecialTxCost) {
            baseFee = yield fetchOwnerReserveFee(client);
        }
        if (signersCount > 0) {
            baseFee = bignumber_js_1.default.sum(baseFee, scaleValue(netFeeDrops, 1 + signersCount));
        }
        const maxFeeDrops = (0, utils_1.xrpToDrops)(client.maxFeeXRP);
        const totalFee = isSpecialTxCost
            ? baseFee
            : bignumber_js_1.default.min(baseFee, maxFeeDrops);
        tx.Fee = totalFee.dp(0, bignumber_js_1.default.ROUND_CEIL).toString(10);
    });
}
exports.calculateFeePerTransactionType = calculateFeePerTransactionType;
function scaleValue(value, multiplier) {
    return new bignumber_js_1.default(value).times(multiplier).toString();
}
function setLatestValidatedLedgerSequence(client, tx) {
    return __awaiter(this, void 0, void 0, function* () {
        const ledgerSequence = yield client.getLedgerIndex();
        tx.LastLedgerSequence = ledgerSequence + LEDGER_OFFSET;
    });
}
exports.setLatestValidatedLedgerSequence = setLatestValidatedLedgerSequence;
function checkAccountDeleteBlockers(client, tx) {
    return __awaiter(this, void 0, void 0, function* () {
        const request = {
            command: 'account_objects',
            account: tx.Account,
            ledger_index: 'validated',
            deletion_blockers_only: true,
        };
        const response = yield client.request(request);
        return new Promise((resolve, reject) => {
            if (response.result.account_objects.length > 0) {
                reject(new errors_1.XrplError(`Account ${tx.Account} cannot be deleted; there are Escrows, PayChannels, RippleStates, or Checks associated with the account.`, response.result.account_objects));
            }
            resolve();
        });
    });
}
exports.checkAccountDeleteBlockers = checkAccountDeleteBlockers;


/***/ }),

/***/ "./dist/npm/sugar/balances.js":
/*!************************************!*\
  !*** ./dist/npm/sugar/balances.js ***!
  \************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.formatBalances = void 0;
function formatBalances(trustlines) {
    return trustlines.map((trustline) => ({
        value: trustline.balance,
        currency: trustline.currency,
        issuer: trustline.account,
    }));
}
exports.formatBalances = formatBalances;


/***/ }),

/***/ "./dist/npm/sugar/getFeeXrp.js":
/*!*************************************!*\
  !*** ./dist/npm/sugar/getFeeXrp.js ***!
  \*************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const NUM_DECIMAL_PLACES = 6;
const BASE_10 = 10;
function getFeeXrp(client, cushion) {
    var _a;
    return __awaiter(this, void 0, void 0, function* () {
        const feeCushion = cushion !== null && cushion !== void 0 ? cushion : client.feeCushion;
        const serverInfo = (yield client.request({
            command: 'server_info',
        })).result.info;
        const baseFee = (_a = serverInfo.validated_ledger) === null || _a === void 0 ? void 0 : _a.base_fee_xrp;
        if (baseFee == null) {
            throw new errors_1.XrplError('getFeeXrp: Could not get base_fee_xrp from server_info');
        }
        const baseFeeXrp = new bignumber_js_1.default(baseFee);
        if (serverInfo.load_factor == null) {
            serverInfo.load_factor = 1;
        }
        let fee = baseFeeXrp.times(serverInfo.load_factor).times(feeCushion);
        fee = bignumber_js_1.default.min(fee, client.maxFeeXRP);
        return new bignumber_js_1.default(fee.toFixed(NUM_DECIMAL_PLACES)).toString(BASE_10);
    });
}
exports["default"] = getFeeXrp;


/***/ }),

/***/ "./dist/npm/sugar/getOrderbook.js":
/*!****************************************!*\
  !*** ./dist/npm/sugar/getOrderbook.js ***!
  \****************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.sortAndLimitOffers = exports.separateBuySellOrders = exports.combineOrders = exports.extractOffers = exports.reverseRequest = exports.requestAllOffers = exports.createBookOffersRequest = exports.validateOrderbookOptions = void 0;
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const Offer_1 = __webpack_require__(/*! ../models/ledger/Offer */ "./dist/npm/models/ledger/Offer.js");
const DEFAULT_LIMIT = 20;
function sortOffers(offers) {
    return offers.sort((offerA, offerB) => {
        var _a, _b;
        const qualityA = (_a = offerA.quality) !== null && _a !== void 0 ? _a : 0;
        const qualityB = (_b = offerB.quality) !== null && _b !== void 0 ? _b : 0;
        return new bignumber_js_1.default(qualityA).comparedTo(qualityB);
    });
}
const getOrderbookOptionsSet = new Set([
    'limit',
    'ledger_index',
    'ledger_hash',
    'taker',
]);
function validateOrderbookOptions(options) {
    for (const key of Object.keys(options)) {
        if (!getOrderbookOptionsSet.has(key)) {
            throw new errors_1.ValidationError(`Unexpected option: ${key}`, options);
        }
    }
    if (options.limit && typeof options.limit !== 'number') {
        throw new errors_1.ValidationError('limit must be a number', options.limit);
    }
    if (options.ledger_index &&
        !(typeof options.ledger_index === 'number' ||
            (typeof options.ledger_index === 'string' &&
                ['validated', 'closed', 'current'].includes(options.ledger_index)))) {
        throw new errors_1.ValidationError('ledger_index must be a number or a string of "validated", "closed", or "current"', options.ledger_index);
    }
    if (options.ledger_hash !== undefined &&
        options.ledger_hash !== null &&
        typeof options.ledger_hash !== 'string') {
        throw new errors_1.ValidationError('ledger_hash must be a string', options.ledger_hash);
    }
    if (options.taker !== undefined && typeof options.taker !== 'string') {
        throw new errors_1.ValidationError('taker must be a string', options.taker);
    }
}
exports.validateOrderbookOptions = validateOrderbookOptions;
function createBookOffersRequest(currency1, currency2, options) {
    var _a, _b;
    const request = {
        command: 'book_offers',
        taker_pays: currency1,
        taker_gets: currency2,
        ledger_index: (_a = options.ledger_index) !== null && _a !== void 0 ? _a : 'validated',
        ledger_hash: options.ledger_hash === null ? undefined : options.ledger_hash,
        limit: (_b = options.limit) !== null && _b !== void 0 ? _b : DEFAULT_LIMIT,
        taker: options.taker ? options.taker : undefined,
    };
    return request;
}
exports.createBookOffersRequest = createBookOffersRequest;
function requestAllOffers(client, request) {
    return __awaiter(this, void 0, void 0, function* () {
        const results = yield client.requestAll(request);
        return results.map((result) => result.result.offers);
    });
}
exports.requestAllOffers = requestAllOffers;
function reverseRequest(request) {
    return Object.assign(Object.assign({}, request), { taker_pays: request.taker_gets, taker_gets: request.taker_pays });
}
exports.reverseRequest = reverseRequest;
function extractOffers(offerResults) {
    return offerResults.flatMap((offerResult) => offerResult);
}
exports.extractOffers = extractOffers;
function combineOrders(directOffers, reverseOffers) {
    return [...directOffers, ...reverseOffers];
}
exports.combineOrders = combineOrders;
function separateBuySellOrders(orders) {
    const buy = [];
    const sell = [];
    orders.forEach((order) => {
        if ((order.Flags & Offer_1.OfferFlags.lsfSell) === 0) {
            buy.push(order);
        }
        else {
            sell.push(order);
        }
    });
    return { buy, sell };
}
exports.separateBuySellOrders = separateBuySellOrders;
function sortAndLimitOffers(offers, limit) {
    const sortedOffers = sortOffers(offers);
    return sortedOffers.slice(0, limit);
}
exports.sortAndLimitOffers = sortAndLimitOffers;


/***/ }),

/***/ "./dist/npm/sugar/index.js":
/*!*********************************!*\
  !*** ./dist/npm/sugar/index.js ***!
  \*********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
__exportStar(__webpack_require__(/*! ./submit */ "./dist/npm/sugar/submit.js"), exports);
__exportStar(__webpack_require__(/*! ./utils */ "./dist/npm/sugar/utils.js"), exports);


/***/ }),

/***/ "./dist/npm/sugar/submit.js":
/*!**********************************!*\
  !*** ./dist/npm/sugar/submit.js ***!
  \**********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __awaiter = (this && this.__awaiter) || function (thisArg, _arguments, P, generator) {
    function adopt(value) { return value instanceof P ? value : new P(function (resolve) { resolve(value); }); }
    return new (P || (P = Promise))(function (resolve, reject) {
        function fulfilled(value) { try { step(generator.next(value)); } catch (e) { reject(e); } }
        function rejected(value) { try { step(generator["throw"](value)); } catch (e) { reject(e); } }
        function step(result) { result.done ? resolve(result.value) : adopt(result.value).then(fulfilled, rejected); }
        step((generator = generator.apply(thisArg, _arguments || [])).next());
    });
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.getLastLedgerSequence = exports.getSignedTx = exports.waitForFinalTransactionOutcome = exports.submitRequest = void 0;
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const utils_1 = __webpack_require__(/*! ../utils */ "./dist/npm/utils/index.js");
const LEDGER_CLOSE_TIME = 1000;
function sleep(ms) {
    return __awaiter(this, void 0, void 0, function* () {
        return new Promise((resolve) => {
            setTimeout(resolve, ms);
        });
    });
}
function submitRequest(client, signedTransaction, failHard = false) {
    return __awaiter(this, void 0, void 0, function* () {
        if (!isSigned(signedTransaction)) {
            throw new errors_1.ValidationError('Transaction must be signed.');
        }
        const signedTxEncoded = typeof signedTransaction === 'string'
            ? signedTransaction
            : (0, utils_1.encode)(signedTransaction);
        const request = {
            command: 'submit',
            tx_blob: signedTxEncoded,
            fail_hard: isAccountDelete(signedTransaction) || failHard,
        };
        return client.request(request);
    });
}
exports.submitRequest = submitRequest;
function waitForFinalTransactionOutcome(client, txHash, lastLedger, submissionResult) {
    return __awaiter(this, void 0, void 0, function* () {
        yield sleep(LEDGER_CLOSE_TIME);
        const latestLedger = yield client.getLedgerIndex();
        if (lastLedger < latestLedger) {
            throw new errors_1.XrplError(`The latest ledger sequence ${latestLedger} is greater than the transaction's LastLedgerSequence (${lastLedger}).\n` +
                `Preliminary result: ${submissionResult}`);
        }
        const txResponse = yield client
            .request({
            command: 'tx',
            transaction: txHash,
        })
            .catch((error) => __awaiter(this, void 0, void 0, function* () {
            var _a;
            const message = (_a = error === null || error === void 0 ? void 0 : error.data) === null || _a === void 0 ? void 0 : _a.error;
            if (message === 'txnNotFound') {
                return waitForFinalTransactionOutcome(client, txHash, lastLedger, submissionResult);
            }
            throw new Error(`${message} \n Preliminary result: ${submissionResult}.\nFull error details: ${String(error)}`);
        }));
        if (txResponse.result.validated) {
            return txResponse;
        }
        return waitForFinalTransactionOutcome(client, txHash, lastLedger, submissionResult);
    });
}
exports.waitForFinalTransactionOutcome = waitForFinalTransactionOutcome;
function isSigned(transaction) {
    const tx = typeof transaction === 'string' ? (0, utils_1.decode)(transaction) : transaction;
    if (typeof tx === 'string') {
        return false;
    }
    if (tx.Signers != null) {
        const signers = tx.Signers;
        for (const signer of signers) {
            if (signer.Signer.SigningPubKey == null ||
                signer.Signer.TxnSignature == null) {
                return false;
            }
        }
        return true;
    }
    return tx.SigningPubKey != null && tx.TxnSignature != null;
}
function getSignedTx(client, transaction, { autofill = true, wallet, } = {}) {
    return __awaiter(this, void 0, void 0, function* () {
        if (isSigned(transaction)) {
            return transaction;
        }
        if (!wallet) {
            throw new errors_1.ValidationError('Wallet must be provided when submitting an unsigned transaction');
        }
        let tx = typeof transaction === 'string'
            ?
                (0, utils_1.decode)(transaction)
            : transaction;
        if (autofill) {
            tx = yield client.autofill(tx);
        }
        return wallet.sign(tx).tx_blob;
    });
}
exports.getSignedTx = getSignedTx;
function getLastLedgerSequence(transaction) {
    const tx = typeof transaction === 'string' ? (0, utils_1.decode)(transaction) : transaction;
    return tx.LastLedgerSequence;
}
exports.getLastLedgerSequence = getLastLedgerSequence;
function isAccountDelete(transaction) {
    const tx = typeof transaction === 'string' ? (0, utils_1.decode)(transaction) : transaction;
    return tx.TransactionType === 'AccountDelete';
}


/***/ }),

/***/ "./dist/npm/sugar/utils.js":
/*!*********************************!*\
  !*** ./dist/npm/sugar/utils.js ***!
  \*********************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.ensureClassicAddress = void 0;
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
function ensureClassicAddress(account) {
    if ((0, ripple_address_codec_1.isValidXAddress)(account)) {
        const { classicAddress, tag } = (0, ripple_address_codec_1.xAddressToClassicAddress)(account);
        if (tag !== false) {
            throw new Error('This command does not support the use of a tag. Use an address without a tag.');
        }
        return classicAddress;
    }
    return account;
}
exports.ensureClassicAddress = ensureClassicAddress;


/***/ }),

/***/ "./dist/npm/utils/collections.js":
/*!***************************************!*\
  !*** ./dist/npm/utils/collections.js ***!
  \***************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.omitBy = exports.groupBy = void 0;
function groupBy(array, iteratee) {
    function predicate(acc, value, index, arrayReference) {
        const key = iteratee(value, index, arrayReference) || 0;
        const group = acc[key] || [];
        group.push(value);
        acc[key] = group;
        return acc;
    }
    return array.reduce(predicate, {});
}
exports.groupBy = groupBy;
function omitBy(obj, predicate) {
    const keys = Object.keys(obj);
    const keysToKeep = keys.filter((kb) => !predicate(obj[kb], kb));
    return keysToKeep.reduce((acc, key) => {
        acc[key] = obj[key];
        return acc;
    }, {});
}
exports.omitBy = omitBy;


/***/ }),

/***/ "./dist/npm/utils/derive.js":
/*!**********************************!*\
  !*** ./dist/npm/utils/derive.js ***!
  \**********************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.deriveXAddress = exports.deriveAddress = exports.deriveKeypair = void 0;
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
Object.defineProperty(exports, "deriveKeypair", ({ enumerable: true, get: function () { return ripple_keypairs_1.deriveKeypair; } }));
Object.defineProperty(exports, "deriveAddress", ({ enumerable: true, get: function () { return ripple_keypairs_1.deriveAddress; } }));
function deriveXAddress(options) {
    const classicAddress = (0, ripple_keypairs_1.deriveAddress)(options.publicKey);
    return (0, ripple_address_codec_1.classicAddressToXAddress)(classicAddress, options.tag, options.test);
}
exports.deriveXAddress = deriveXAddress;


/***/ }),

/***/ "./dist/npm/utils/getBalanceChanges.js":
/*!*********************************************!*\
  !*** ./dist/npm/utils/getBalanceChanges.js ***!
  \*********************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const collections_1 = __webpack_require__(/*! ./collections */ "./dist/npm/utils/collections.js");
const xrpConversion_1 = __webpack_require__(/*! ./xrpConversion */ "./dist/npm/utils/xrpConversion.js");
function normalizeNode(affectedNode) {
    const diffType = Object.keys(affectedNode)[0];
    const node = affectedNode[diffType];
    return Object.assign(Object.assign({}, node), { NodeType: diffType, LedgerEntryType: node.LedgerEntryType, LedgerIndex: node.LedgerIndex, NewFields: node.NewFields, FinalFields: node.FinalFields, PreviousFields: node.PreviousFields });
}
function normalizeNodes(metadata) {
    if (metadata.AffectedNodes.length === 0) {
        return [];
    }
    return metadata.AffectedNodes.map(normalizeNode);
}
function groupByAccount(balanceChanges) {
    const grouped = (0, collections_1.groupBy)(balanceChanges, (node) => node.account);
    return Object.entries(grouped).map(([account, items]) => {
        return { account, balances: items.map((item) => item.balance) };
    });
}
function getValue(balance) {
    if (typeof balance === 'string') {
        return new bignumber_js_1.default(balance);
    }
    return new bignumber_js_1.default(balance.value);
}
function computeBalanceChange(node) {
    var _a, _b, _c;
    let value = null;
    if ((_a = node.NewFields) === null || _a === void 0 ? void 0 : _a.Balance) {
        value = getValue(node.NewFields.Balance);
    }
    else if (((_b = node.PreviousFields) === null || _b === void 0 ? void 0 : _b.Balance) && ((_c = node.FinalFields) === null || _c === void 0 ? void 0 : _c.Balance)) {
        value = getValue(node.FinalFields.Balance).minus(getValue(node.PreviousFields.Balance));
    }
    if (value === null || value.isZero()) {
        return null;
    }
    return value;
}
function getXRPQuantity(node) {
    var _a, _b, _c;
    const value = computeBalanceChange(node);
    if (value === null) {
        return null;
    }
    return {
        account: ((_b = (_a = node.FinalFields) === null || _a === void 0 ? void 0 : _a.Account) !== null && _b !== void 0 ? _b : (_c = node.NewFields) === null || _c === void 0 ? void 0 : _c.Account),
        balance: {
            currency: 'XRP',
            value: (0, xrpConversion_1.dropsToXrp)(value).toString(),
        },
    };
}
function flipTrustlinePerspective(balanceChange) {
    const negatedBalance = new bignumber_js_1.default(balanceChange.balance.value).negated();
    return {
        account: balanceChange.balance.issuer,
        balance: {
            issuer: balanceChange.account,
            currency: balanceChange.balance.currency,
            value: negatedBalance.toString(),
        },
    };
}
function getTrustlineQuantity(node) {
    var _a, _b;
    const value = computeBalanceChange(node);
    if (value === null) {
        return null;
    }
    const fields = node.NewFields == null ? node.FinalFields : node.NewFields;
    const result = {
        account: (_a = fields === null || fields === void 0 ? void 0 : fields.LowLimit) === null || _a === void 0 ? void 0 : _a.issuer,
        balance: {
            issuer: (_b = fields === null || fields === void 0 ? void 0 : fields.HighLimit) === null || _b === void 0 ? void 0 : _b.issuer,
            currency: (fields === null || fields === void 0 ? void 0 : fields.Balance).currency,
            value: value.toString(),
        },
    };
    return [result, flipTrustlinePerspective(result)];
}
function getBalanceChanges(metadata) {
    const quantities = normalizeNodes(metadata).map((node) => {
        if (node.LedgerEntryType === 'AccountRoot') {
            const xrpQuantity = getXRPQuantity(node);
            if (xrpQuantity == null) {
                return [];
            }
            return [xrpQuantity];
        }
        if (node.LedgerEntryType === 'RippleState') {
            const trustlineQuantity = getTrustlineQuantity(node);
            if (trustlineQuantity == null) {
                return [];
            }
            return trustlineQuantity;
        }
        return [];
    });
    return groupByAccount(quantities.flat());
}
exports["default"] = getBalanceChanges;


/***/ }),

/***/ "./dist/npm/utils/getNFTokenID.js":
/*!****************************************!*\
  !*** ./dist/npm/utils/getNFTokenID.js ***!
  \****************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const metadata_1 = __webpack_require__(/*! ../models/transactions/metadata */ "./dist/npm/models/transactions/metadata.js");
function ensureDecodedMeta(meta) {
    if (typeof meta === 'string') {
        return (0, ripple_binary_codec_1.decode)(meta);
    }
    return meta;
}
function getNFTokenID(meta) {
    if (typeof meta !== 'string' && (meta === null || meta === void 0 ? void 0 : meta.AffectedNodes) === undefined) {
        throw new TypeError(`Unable to parse the parameter given to getNFTokenID.
      'meta' must be the metadata from an NFTokenMint transaction. Received ${JSON.stringify(meta)} instead.`);
    }
    const decodedMeta = ensureDecodedMeta(meta);
    const affectedNodes = decodedMeta.AffectedNodes.filter((node) => {
        var _a;
        if ((0, metadata_1.isCreatedNode)(node)) {
            return node.CreatedNode.LedgerEntryType === 'NFTokenPage';
        }
        if ((0, metadata_1.isModifiedNode)(node)) {
            return (node.ModifiedNode.LedgerEntryType === 'NFTokenPage' &&
                Boolean((_a = node.ModifiedNode.PreviousFields) === null || _a === void 0 ? void 0 : _a.NFTokens));
        }
        return false;
    });
    const previousTokenIDSet = new Set(affectedNodes
        .flatMap((node) => {
        var _a;
        const nftokens = (0, metadata_1.isModifiedNode)(node)
            ? (_a = node.ModifiedNode.PreviousFields) === null || _a === void 0 ? void 0 : _a.NFTokens
            : [];
        return nftokens.map((token) => token.NFToken.NFTokenID);
    })
        .filter((id) => Boolean(id)));
    const finalTokenIDs = affectedNodes
        .flatMap((node) => {
        var _a, _b, _c, _d, _e, _f;
        return ((_f = ((_c = (_b = (_a = node.ModifiedNode) === null || _a === void 0 ? void 0 : _a.FinalFields) === null || _b === void 0 ? void 0 : _b.NFTokens) !== null && _c !== void 0 ? _c : (_e = (_d = node.CreatedNode) === null || _d === void 0 ? void 0 : _d.NewFields) === null || _e === void 0 ? void 0 : _e.NFTokens)) !== null && _f !== void 0 ? _f : []).map((token) => token.NFToken.NFTokenID);
    })
        .filter((nftokenID) => Boolean(nftokenID));
    const nftokenID = finalTokenIDs.find((id) => !previousTokenIDSet.has(id));
    return nftokenID;
}
exports["default"] = getNFTokenID;


/***/ }),

/***/ "./dist/npm/utils/getXChainClaimID.js":
/*!********************************************!*\
  !*** ./dist/npm/utils/getXChainClaimID.js ***!
  \********************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const metadata_1 = __webpack_require__(/*! ../models/transactions/metadata */ "./dist/npm/models/transactions/metadata.js");
function ensureDecodedMeta(meta) {
    if (typeof meta === 'string') {
        return (0, ripple_binary_codec_1.decode)(meta);
    }
    return meta;
}
function getXChainClaimID(meta) {
    if (typeof meta !== 'string' && (meta === null || meta === void 0 ? void 0 : meta.AffectedNodes) === undefined) {
        throw new TypeError(`Unable to parse the parameter given to getXChainClaimID.
      'meta' must be the metadata from an XChainCreateClaimID transaction. Received ${JSON.stringify(meta)} instead.`);
    }
    const decodedMeta = ensureDecodedMeta(meta);
    if (!decodedMeta.TransactionResult) {
        throw new TypeError('Cannot get XChainClaimID from un-validated transaction');
    }
    if (decodedMeta.TransactionResult !== 'tesSUCCESS') {
        return undefined;
    }
    const createdNode = decodedMeta.AffectedNodes.find((node) => (0, metadata_1.isCreatedNode)(node) &&
        node.CreatedNode.LedgerEntryType === 'XChainOwnedClaimID');
    return createdNode.CreatedNode.NewFields
        .XChainClaimID;
}
exports["default"] = getXChainClaimID;


/***/ }),

/***/ "./dist/npm/utils/hashes/HashPrefix.js":
/*!*********************************************!*\
  !*** ./dist/npm/utils/hashes/HashPrefix.js ***!
  \*********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
var HashPrefix;
(function (HashPrefix) {
    HashPrefix[HashPrefix["TRANSACTION_ID"] = 1415073280] = "TRANSACTION_ID";
    HashPrefix[HashPrefix["TRANSACTION_NODE"] = 1397638144] = "TRANSACTION_NODE";
    HashPrefix[HashPrefix["INNER_NODE"] = 1296649728] = "INNER_NODE";
    HashPrefix[HashPrefix["LEAF_NODE"] = 1296846336] = "LEAF_NODE";
    HashPrefix[HashPrefix["TRANSACTION_SIGN"] = 1398036480] = "TRANSACTION_SIGN";
    HashPrefix[HashPrefix["TRANSACTION_SIGN_TESTNET"] = 1937012736] = "TRANSACTION_SIGN_TESTNET";
    HashPrefix[HashPrefix["TRANSACTION_MULTISIGN"] = 1397576704] = "TRANSACTION_MULTISIGN";
    HashPrefix[HashPrefix["LEDGER"] = 1280791040] = "LEDGER";
})(HashPrefix || (HashPrefix = {}));
exports["default"] = HashPrefix;


/***/ }),

/***/ "./dist/npm/utils/hashes/SHAMap/InnerNode.js":
/*!***************************************************!*\
  !*** ./dist/npm/utils/hashes/SHAMap/InnerNode.js ***!
  \***************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const errors_1 = __webpack_require__(/*! ../../../errors */ "./dist/npm/errors.js");
const HashPrefix_1 = __importDefault(__webpack_require__(/*! ../HashPrefix */ "./dist/npm/utils/hashes/HashPrefix.js"));
const sha512Half_1 = __importDefault(__webpack_require__(/*! ../sha512Half */ "./dist/npm/utils/hashes/sha512Half.js"));
const LeafNode_1 = __importDefault(__webpack_require__(/*! ./LeafNode */ "./dist/npm/utils/hashes/SHAMap/LeafNode.js"));
const node_1 = __webpack_require__(/*! ./node */ "./dist/npm/utils/hashes/SHAMap/node.js");
const HEX_ZERO = '0000000000000000000000000000000000000000000000000000000000000000';
const SLOT_MAX = 15;
const HEX = 16;
class InnerNode extends node_1.Node {
    constructor(depth = 0) {
        super();
        this.leaves = {};
        this.type = node_1.NodeType.INNER;
        this.depth = depth;
        this.empty = true;
    }
    get hash() {
        if (this.empty) {
            return HEX_ZERO;
        }
        let hex = '';
        for (let iter = 0; iter <= SLOT_MAX; iter++) {
            const child = this.leaves[iter];
            const hash = child == null ? HEX_ZERO : child.hash;
            hex += hash;
        }
        const prefix = HashPrefix_1.default.INNER_NODE.toString(HEX);
        return (0, sha512Half_1.default)(prefix + hex);
    }
    addItem(tag, node) {
        const existingNode = this.getNode(parseInt(tag[this.depth], HEX));
        if (existingNode === undefined) {
            this.setNode(parseInt(tag[this.depth], HEX), node);
            return;
        }
        if (existingNode instanceof InnerNode) {
            existingNode.addItem(tag, node);
        }
        else if (existingNode instanceof LeafNode_1.default) {
            if (existingNode.tag === tag) {
                throw new errors_1.XrplError('Tried to add a node to a SHAMap that was already in there.');
            }
            else {
                const newInnerNode = new InnerNode(this.depth + 1);
                newInnerNode.addItem(existingNode.tag, existingNode);
                newInnerNode.addItem(tag, node);
                this.setNode(parseInt(tag[this.depth], HEX), newInnerNode);
            }
        }
    }
    setNode(slot, node) {
        if (slot < 0 || slot > SLOT_MAX) {
            throw new errors_1.XrplError('Invalid slot: slot must be between 0-15.');
        }
        this.leaves[slot] = node;
        this.empty = false;
    }
    getNode(slot) {
        if (slot < 0 || slot > SLOT_MAX) {
            throw new errors_1.XrplError('Invalid slot: slot must be between 0-15.');
        }
        return this.leaves[slot];
    }
}
exports["default"] = InnerNode;


/***/ }),

/***/ "./dist/npm/utils/hashes/SHAMap/LeafNode.js":
/*!**************************************************!*\
  !*** ./dist/npm/utils/hashes/SHAMap/LeafNode.js ***!
  \**************************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const errors_1 = __webpack_require__(/*! ../../../errors */ "./dist/npm/errors.js");
const HashPrefix_1 = __importDefault(__webpack_require__(/*! ../HashPrefix */ "./dist/npm/utils/hashes/HashPrefix.js"));
const sha512Half_1 = __importDefault(__webpack_require__(/*! ../sha512Half */ "./dist/npm/utils/hashes/sha512Half.js"));
const node_1 = __webpack_require__(/*! ./node */ "./dist/npm/utils/hashes/SHAMap/node.js");
const HEX = 16;
class LeafNode extends node_1.Node {
    constructor(tag, data, type) {
        super();
        this.tag = tag;
        this.type = type;
        this.data = data;
    }
    get hash() {
        switch (this.type) {
            case node_1.NodeType.ACCOUNT_STATE: {
                const leafPrefix = HashPrefix_1.default.LEAF_NODE.toString(HEX);
                return (0, sha512Half_1.default)(leafPrefix + this.data + this.tag);
            }
            case node_1.NodeType.TRANSACTION_NO_METADATA: {
                const txIDPrefix = HashPrefix_1.default.TRANSACTION_ID.toString(HEX);
                return (0, sha512Half_1.default)(txIDPrefix + this.data);
            }
            case node_1.NodeType.TRANSACTION_METADATA: {
                const txNodePrefix = HashPrefix_1.default.TRANSACTION_NODE.toString(HEX);
                return (0, sha512Half_1.default)(txNodePrefix + this.data + this.tag);
            }
            default:
                throw new errors_1.XrplError('Tried to hash a SHAMap node of unknown type.');
        }
    }
    addItem(tag, node) {
        throw new errors_1.XrplError('Cannot call addItem on a LeafNode');
        this.addItem(tag, node);
    }
}
exports["default"] = LeafNode;


/***/ }),

/***/ "./dist/npm/utils/hashes/SHAMap/index.js":
/*!***********************************************!*\
  !*** ./dist/npm/utils/hashes/SHAMap/index.js ***!
  \***********************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __exportStar = (this && this.__exportStar) || function(m, exports) {
    for (var p in m) if (p !== "default" && !Object.prototype.hasOwnProperty.call(exports, p)) __createBinding(exports, m, p);
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const InnerNode_1 = __importDefault(__webpack_require__(/*! ./InnerNode */ "./dist/npm/utils/hashes/SHAMap/InnerNode.js"));
const LeafNode_1 = __importDefault(__webpack_require__(/*! ./LeafNode */ "./dist/npm/utils/hashes/SHAMap/LeafNode.js"));
class SHAMap {
    constructor() {
        this.root = new InnerNode_1.default(0);
    }
    get hash() {
        return this.root.hash;
    }
    addItem(tag, data, type) {
        this.root.addItem(tag, new LeafNode_1.default(tag, data, type));
    }
}
__exportStar(__webpack_require__(/*! ./node */ "./dist/npm/utils/hashes/SHAMap/node.js"), exports);
exports["default"] = SHAMap;


/***/ }),

/***/ "./dist/npm/utils/hashes/SHAMap/node.js":
/*!**********************************************!*\
  !*** ./dist/npm/utils/hashes/SHAMap/node.js ***!
  \**********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.Node = exports.NodeType = void 0;
var NodeType;
(function (NodeType) {
    NodeType[NodeType["INNER"] = 1] = "INNER";
    NodeType[NodeType["TRANSACTION_NO_METADATA"] = 2] = "TRANSACTION_NO_METADATA";
    NodeType[NodeType["TRANSACTION_METADATA"] = 3] = "TRANSACTION_METADATA";
    NodeType[NodeType["ACCOUNT_STATE"] = 4] = "ACCOUNT_STATE";
})(NodeType || (exports.NodeType = NodeType = {}));
class Node {
}
exports.Node = Node;


/***/ }),

/***/ "./dist/npm/utils/hashes/hashLedger.js":
/*!*********************************************!*\
  !*** ./dist/npm/utils/hashes/hashLedger.js ***!
  \*********************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.hashStateTree = exports.hashTxTree = exports.hashLedgerHeader = exports.hashSignedTx = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const errors_1 = __webpack_require__(/*! ../../errors */ "./dist/npm/errors.js");
const HashPrefix_1 = __importDefault(__webpack_require__(/*! ./HashPrefix */ "./dist/npm/utils/hashes/HashPrefix.js"));
const sha512Half_1 = __importDefault(__webpack_require__(/*! ./sha512Half */ "./dist/npm/utils/hashes/sha512Half.js"));
const SHAMap_1 = __importStar(__webpack_require__(/*! ./SHAMap */ "./dist/npm/utils/hashes/SHAMap/index.js"));
const HEX = 16;
function intToHex(integer, byteLength) {
    const foo = Number(integer)
        .toString(HEX)
        .padStart(byteLength * 2, '0');
    return foo;
}
function bigintToHex(integerString, byteLength) {
    const hex = new bignumber_js_1.default(integerString).toString(HEX);
    return hex.padStart(byteLength * 2, '0');
}
function addLengthPrefix(hex) {
    const length = hex.length / 2;
    if (length <= 192) {
        return (0, utils_1.bytesToHex)([length]) + hex;
    }
    if (length <= 12480) {
        const prefix = length - 193;
        return (0, utils_1.bytesToHex)([193 + (prefix >>> 8), prefix & 0xff]) + hex;
    }
    if (length <= 918744) {
        const prefix = length - 12481;
        return ((0, utils_1.bytesToHex)([
            241 + (prefix >>> 16),
            (prefix >>> 8) & 0xff,
            prefix & 0xff,
        ]) + hex);
    }
    throw new errors_1.XrplError('Variable integer overflow.');
}
function hashSignedTx(tx) {
    let txBlob;
    let txObject;
    if (typeof tx === 'string') {
        txBlob = tx;
        txObject = (0, ripple_binary_codec_1.decode)(tx);
    }
    else {
        txBlob = (0, ripple_binary_codec_1.encode)(tx);
        txObject = tx;
    }
    if (txObject.TxnSignature === undefined &&
        txObject.Signers === undefined &&
        txObject.SigningPubKey === undefined) {
        throw new errors_1.ValidationError('The transaction must be signed to hash it.');
    }
    const prefix = HashPrefix_1.default.TRANSACTION_ID.toString(16).toUpperCase();
    return (0, sha512Half_1.default)(prefix.concat(txBlob));
}
exports.hashSignedTx = hashSignedTx;
function hashLedgerHeader(ledgerHeader) {
    const prefix = HashPrefix_1.default.LEDGER.toString(HEX).toUpperCase();
    const ledger = prefix +
        intToHex(Number(ledgerHeader.ledger_index), 4) +
        bigintToHex(ledgerHeader.total_coins, 8) +
        ledgerHeader.parent_hash +
        ledgerHeader.transaction_hash +
        ledgerHeader.account_hash +
        intToHex(ledgerHeader.parent_close_time, 4) +
        intToHex(ledgerHeader.close_time, 4) +
        intToHex(ledgerHeader.close_time_resolution, 1) +
        intToHex(ledgerHeader.close_flags, 1);
    return (0, sha512Half_1.default)(ledger);
}
exports.hashLedgerHeader = hashLedgerHeader;
function hashTxTree(transactions) {
    var _a;
    const shamap = new SHAMap_1.default();
    for (const txJSON of transactions) {
        const txBlobHex = (0, ripple_binary_codec_1.encode)(txJSON);
        const metaHex = (0, ripple_binary_codec_1.encode)((_a = txJSON.metaData) !== null && _a !== void 0 ? _a : {});
        const txHash = hashSignedTx(txBlobHex);
        const data = addLengthPrefix(txBlobHex) + addLengthPrefix(metaHex);
        shamap.addItem(txHash, data, SHAMap_1.NodeType.TRANSACTION_METADATA);
    }
    return shamap.hash;
}
exports.hashTxTree = hashTxTree;
function hashStateTree(entries) {
    const shamap = new SHAMap_1.default();
    entries.forEach((ledgerEntry) => {
        const data = (0, ripple_binary_codec_1.encode)(ledgerEntry);
        shamap.addItem(ledgerEntry.index, data, SHAMap_1.NodeType.ACCOUNT_STATE);
    });
    return shamap.hash;
}
exports.hashStateTree = hashStateTree;
function computeTransactionHash(ledger, options) {
    const { transaction_hash } = ledger;
    if (!options.computeTreeHashes) {
        return transaction_hash;
    }
    if (ledger.transactions == null) {
        throw new errors_1.ValidationError('transactions is missing from the ledger');
    }
    const transactionHash = hashTxTree(ledger.transactions);
    if (transaction_hash !== transactionHash) {
        throw new errors_1.ValidationError('transactionHash in header' +
            ' does not match computed hash of transactions', {
            transactionHashInHeader: transaction_hash,
            computedHashOfTransactions: transactionHash,
        });
    }
    return transactionHash;
}
function computeStateHash(ledger, options) {
    const { account_hash } = ledger;
    if (!options.computeTreeHashes) {
        return account_hash;
    }
    if (ledger.accountState == null) {
        throw new errors_1.ValidationError('accountState is missing from the ledger');
    }
    const stateHash = hashStateTree(ledger.accountState);
    if (account_hash !== stateHash) {
        throw new errors_1.ValidationError('stateHash in header does not match computed hash of state');
    }
    return stateHash;
}
function hashLedger(ledger, options = {}) {
    const subhashes = {
        transaction_hash: computeTransactionHash(ledger, options),
        account_hash: computeStateHash(ledger, options),
    };
    return hashLedgerHeader(Object.assign(Object.assign({}, ledger), subhashes));
}
exports["default"] = hashLedger;


/***/ }),

/***/ "./dist/npm/utils/hashes/index.js":
/*!****************************************!*\
  !*** ./dist/npm/utils/hashes/index.js ***!
  \****************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || function (mod) {
    if (mod && mod.__esModule) return mod;
    var result = {};
    if (mod != null) for (var k in mod) if (k !== "default" && Object.prototype.hasOwnProperty.call(mod, k)) __createBinding(result, mod, k);
    __setModuleDefault(result, mod);
    return result;
};
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.hashTxTree = exports.hashStateTree = exports.hashLedger = exports.hashSignedTx = exports.hashLedgerHeader = exports.hashPaymentChannel = exports.hashEscrow = exports.hashTrustline = exports.hashOfferId = exports.hashSignerListId = exports.hashAccountRoot = exports.hashTx = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const hashLedger_1 = __importStar(__webpack_require__(/*! ./hashLedger */ "./dist/npm/utils/hashes/hashLedger.js"));
exports.hashLedger = hashLedger_1.default;
Object.defineProperty(exports, "hashLedgerHeader", ({ enumerable: true, get: function () { return hashLedger_1.hashLedgerHeader; } }));
Object.defineProperty(exports, "hashSignedTx", ({ enumerable: true, get: function () { return hashLedger_1.hashSignedTx; } }));
Object.defineProperty(exports, "hashTxTree", ({ enumerable: true, get: function () { return hashLedger_1.hashTxTree; } }));
Object.defineProperty(exports, "hashStateTree", ({ enumerable: true, get: function () { return hashLedger_1.hashStateTree; } }));
const HashPrefix_1 = __importDefault(__webpack_require__(/*! ./HashPrefix */ "./dist/npm/utils/hashes/HashPrefix.js"));
const ledgerSpaces_1 = __importDefault(__webpack_require__(/*! ./ledgerSpaces */ "./dist/npm/utils/hashes/ledgerSpaces.js"));
const sha512Half_1 = __importDefault(__webpack_require__(/*! ./sha512Half */ "./dist/npm/utils/hashes/sha512Half.js"));
const HEX = 16;
const BYTE_LENGTH = 4;
function addressToHex(address) {
    return (0, utils_1.bytesToHex)((0, ripple_address_codec_1.decodeAccountID)(address));
}
function ledgerSpaceHex(name) {
    return ledgerSpaces_1.default[name].charCodeAt(0).toString(HEX).padStart(4, '0');
}
const MASK = 0xff;
function currencyToHex(currency) {
    if (currency.length !== 3) {
        return currency;
    }
    const bytes = Array(20).fill(0);
    bytes[12] = currency.charCodeAt(0) & MASK;
    bytes[13] = currency.charCodeAt(1) & MASK;
    bytes[14] = currency.charCodeAt(2) & MASK;
    return (0, utils_1.bytesToHex)(Uint8Array.from(bytes));
}
function hashTx(txBlobHex) {
    const prefix = HashPrefix_1.default.TRANSACTION_SIGN.toString(HEX).toUpperCase();
    return (0, sha512Half_1.default)(prefix + txBlobHex);
}
exports.hashTx = hashTx;
function hashAccountRoot(address) {
    return (0, sha512Half_1.default)(ledgerSpaceHex('account') + addressToHex(address));
}
exports.hashAccountRoot = hashAccountRoot;
function hashSignerListId(address) {
    return (0, sha512Half_1.default)(`${ledgerSpaceHex('signerList') + addressToHex(address)}00000000`);
}
exports.hashSignerListId = hashSignerListId;
function hashOfferId(address, sequence) {
    const hexPrefix = ledgerSpaces_1.default.offer
        .charCodeAt(0)
        .toString(HEX)
        .padStart(2, '0');
    const hexSequence = sequence.toString(HEX).padStart(8, '0');
    const prefix = `00${hexPrefix}`;
    return (0, sha512Half_1.default)(prefix + addressToHex(address) + hexSequence);
}
exports.hashOfferId = hashOfferId;
function hashTrustline(address1, address2, currency) {
    const address1Hex = addressToHex(address1);
    const address2Hex = addressToHex(address2);
    const swap = new bignumber_js_1.default(address1Hex, 16).isGreaterThan(new bignumber_js_1.default(address2Hex, 16));
    const lowAddressHex = swap ? address2Hex : address1Hex;
    const highAddressHex = swap ? address1Hex : address2Hex;
    const prefix = ledgerSpaceHex('rippleState');
    return (0, sha512Half_1.default)(prefix + lowAddressHex + highAddressHex + currencyToHex(currency));
}
exports.hashTrustline = hashTrustline;
function hashEscrow(address, sequence) {
    return (0, sha512Half_1.default)(ledgerSpaceHex('escrow') +
        addressToHex(address) +
        sequence.toString(HEX).padStart(BYTE_LENGTH * 2, '0'));
}
exports.hashEscrow = hashEscrow;
function hashPaymentChannel(address, dstAddress, sequence) {
    return (0, sha512Half_1.default)(ledgerSpaceHex('paychan') +
        addressToHex(address) +
        addressToHex(dstAddress) +
        sequence.toString(HEX).padStart(BYTE_LENGTH * 2, '0'));
}
exports.hashPaymentChannel = hashPaymentChannel;


/***/ }),

/***/ "./dist/npm/utils/hashes/ledgerSpaces.js":
/*!***********************************************!*\
  !*** ./dist/npm/utils/hashes/ledgerSpaces.js ***!
  \***********************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const ledgerSpaces = {
    account: 'a',
    dirNode: 'd',
    generatorMap: 'g',
    rippleState: 'r',
    offer: 'o',
    ownerDir: 'O',
    bookDir: 'B',
    contract: 'c',
    skipList: 's',
    escrow: 'u',
    amendment: 'f',
    feeSettings: 'e',
    ticket: 'T',
    signerList: 'S',
    paychan: 'x',
    check: 'C',
    depositPreauth: 'p',
};
exports["default"] = ledgerSpaces;


/***/ }),

/***/ "./dist/npm/utils/hashes/sha512Half.js":
/*!*********************************************!*\
  !*** ./dist/npm/utils/hashes/sha512Half.js ***!
  \*********************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const sha512_1 = __webpack_require__(/*! @xrplf/isomorphic/sha512 */ "../../node_modules/@xrplf/isomorphic/dist/sha512/browser.js");
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const HASH_BYTES = 32;
function sha512Half(hex) {
    return (0, utils_1.bytesToHex)((0, sha512_1.sha512)((0, utils_1.hexToBytes)(hex)).slice(0, HASH_BYTES));
}
exports["default"] = sha512Half;


/***/ }),

/***/ "./dist/npm/utils/index.js":
/*!*********************************!*\
  !*** ./dist/npm/utils/index.js ***!
  \*********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.getXChainClaimID = exports.parseNFTokenID = exports.getNFTokenID = exports.encodeForSigningClaim = exports.encodeForSigning = exports.encodeForMultiSigning = exports.decode = exports.encode = exports.decodeXAddress = exports.encodeXAddress = exports.decodeAccountPublic = exports.encodeAccountPublic = exports.decodeNodePublic = exports.encodeNodePublic = exports.decodeAccountID = exports.encodeAccountID = exports.decodeSeed = exports.encodeSeed = exports.isValidClassicAddress = exports.isValidXAddress = exports.xAddressToClassicAddress = exports.classicAddressToXAddress = exports.convertHexToString = exports.convertStringToHex = exports.verifyPaymentChannelClaim = exports.verifyKeypairSignature = exports.signPaymentChannelClaim = exports.deriveXAddress = exports.deriveAddress = exports.deriveKeypair = exports.hashes = exports.isValidAddress = exports.isValidSecret = exports.qualityToDecimal = exports.transferRateToDecimal = exports.decimalToTransferRate = exports.percentToTransferRate = exports.decimalToQuality = exports.percentToQuality = exports.unixTimeToRippleTime = exports.rippleTimeToUnixTime = exports.isoTimeToRippleTime = exports.rippleTimeToISOTime = exports.hasNextPage = exports.xrpToDrops = exports.dropsToXrp = exports.getBalanceChanges = void 0;
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
Object.defineProperty(exports, "classicAddressToXAddress", ({ enumerable: true, get: function () { return ripple_address_codec_1.classicAddressToXAddress; } }));
Object.defineProperty(exports, "decodeAccountID", ({ enumerable: true, get: function () { return ripple_address_codec_1.decodeAccountID; } }));
Object.defineProperty(exports, "decodeAccountPublic", ({ enumerable: true, get: function () { return ripple_address_codec_1.decodeAccountPublic; } }));
Object.defineProperty(exports, "decodeNodePublic", ({ enumerable: true, get: function () { return ripple_address_codec_1.decodeNodePublic; } }));
Object.defineProperty(exports, "decodeSeed", ({ enumerable: true, get: function () { return ripple_address_codec_1.decodeSeed; } }));
Object.defineProperty(exports, "decodeXAddress", ({ enumerable: true, get: function () { return ripple_address_codec_1.decodeXAddress; } }));
Object.defineProperty(exports, "encodeAccountID", ({ enumerable: true, get: function () { return ripple_address_codec_1.encodeAccountID; } }));
Object.defineProperty(exports, "encodeAccountPublic", ({ enumerable: true, get: function () { return ripple_address_codec_1.encodeAccountPublic; } }));
Object.defineProperty(exports, "encodeNodePublic", ({ enumerable: true, get: function () { return ripple_address_codec_1.encodeNodePublic; } }));
Object.defineProperty(exports, "encodeSeed", ({ enumerable: true, get: function () { return ripple_address_codec_1.encodeSeed; } }));
Object.defineProperty(exports, "encodeXAddress", ({ enumerable: true, get: function () { return ripple_address_codec_1.encodeXAddress; } }));
Object.defineProperty(exports, "isValidClassicAddress", ({ enumerable: true, get: function () { return ripple_address_codec_1.isValidClassicAddress; } }));
Object.defineProperty(exports, "isValidXAddress", ({ enumerable: true, get: function () { return ripple_address_codec_1.isValidXAddress; } }));
Object.defineProperty(exports, "xAddressToClassicAddress", ({ enumerable: true, get: function () { return ripple_address_codec_1.xAddressToClassicAddress; } }));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
Object.defineProperty(exports, "verifyKeypairSignature", ({ enumerable: true, get: function () { return ripple_keypairs_1.verify; } }));
const derive_1 = __webpack_require__(/*! ./derive */ "./dist/npm/utils/derive.js");
Object.defineProperty(exports, "deriveKeypair", ({ enumerable: true, get: function () { return derive_1.deriveKeypair; } }));
Object.defineProperty(exports, "deriveAddress", ({ enumerable: true, get: function () { return derive_1.deriveAddress; } }));
Object.defineProperty(exports, "deriveXAddress", ({ enumerable: true, get: function () { return derive_1.deriveXAddress; } }));
const getBalanceChanges_1 = __importDefault(__webpack_require__(/*! ./getBalanceChanges */ "./dist/npm/utils/getBalanceChanges.js"));
exports.getBalanceChanges = getBalanceChanges_1.default;
const getNFTokenID_1 = __importDefault(__webpack_require__(/*! ./getNFTokenID */ "./dist/npm/utils/getNFTokenID.js"));
exports.getNFTokenID = getNFTokenID_1.default;
const getXChainClaimID_1 = __importDefault(__webpack_require__(/*! ./getXChainClaimID */ "./dist/npm/utils/getXChainClaimID.js"));
exports.getXChainClaimID = getXChainClaimID_1.default;
const hashes_1 = __webpack_require__(/*! ./hashes */ "./dist/npm/utils/hashes/index.js");
const parseNFTokenID_1 = __importDefault(__webpack_require__(/*! ./parseNFTokenID */ "./dist/npm/utils/parseNFTokenID.js"));
exports.parseNFTokenID = parseNFTokenID_1.default;
const quality_1 = __webpack_require__(/*! ./quality */ "./dist/npm/utils/quality.js");
Object.defineProperty(exports, "percentToTransferRate", ({ enumerable: true, get: function () { return quality_1.percentToTransferRate; } }));
Object.defineProperty(exports, "decimalToTransferRate", ({ enumerable: true, get: function () { return quality_1.decimalToTransferRate; } }));
Object.defineProperty(exports, "transferRateToDecimal", ({ enumerable: true, get: function () { return quality_1.transferRateToDecimal; } }));
Object.defineProperty(exports, "percentToQuality", ({ enumerable: true, get: function () { return quality_1.percentToQuality; } }));
Object.defineProperty(exports, "decimalToQuality", ({ enumerable: true, get: function () { return quality_1.decimalToQuality; } }));
Object.defineProperty(exports, "qualityToDecimal", ({ enumerable: true, get: function () { return quality_1.qualityToDecimal; } }));
const signPaymentChannelClaim_1 = __importDefault(__webpack_require__(/*! ./signPaymentChannelClaim */ "./dist/npm/utils/signPaymentChannelClaim.js"));
exports.signPaymentChannelClaim = signPaymentChannelClaim_1.default;
const stringConversion_1 = __webpack_require__(/*! ./stringConversion */ "./dist/npm/utils/stringConversion.js");
Object.defineProperty(exports, "convertHexToString", ({ enumerable: true, get: function () { return stringConversion_1.convertHexToString; } }));
Object.defineProperty(exports, "convertStringToHex", ({ enumerable: true, get: function () { return stringConversion_1.convertStringToHex; } }));
const timeConversion_1 = __webpack_require__(/*! ./timeConversion */ "./dist/npm/utils/timeConversion.js");
Object.defineProperty(exports, "rippleTimeToISOTime", ({ enumerable: true, get: function () { return timeConversion_1.rippleTimeToISOTime; } }));
Object.defineProperty(exports, "isoTimeToRippleTime", ({ enumerable: true, get: function () { return timeConversion_1.isoTimeToRippleTime; } }));
Object.defineProperty(exports, "rippleTimeToUnixTime", ({ enumerable: true, get: function () { return timeConversion_1.rippleTimeToUnixTime; } }));
Object.defineProperty(exports, "unixTimeToRippleTime", ({ enumerable: true, get: function () { return timeConversion_1.unixTimeToRippleTime; } }));
const verifyPaymentChannelClaim_1 = __importDefault(__webpack_require__(/*! ./verifyPaymentChannelClaim */ "./dist/npm/utils/verifyPaymentChannelClaim.js"));
exports.verifyPaymentChannelClaim = verifyPaymentChannelClaim_1.default;
const xrpConversion_1 = __webpack_require__(/*! ./xrpConversion */ "./dist/npm/utils/xrpConversion.js");
Object.defineProperty(exports, "xrpToDrops", ({ enumerable: true, get: function () { return xrpConversion_1.xrpToDrops; } }));
Object.defineProperty(exports, "dropsToXrp", ({ enumerable: true, get: function () { return xrpConversion_1.dropsToXrp; } }));
function isValidSecret(secret) {
    try {
        (0, derive_1.deriveKeypair)(secret);
        return true;
    }
    catch (_err) {
        return false;
    }
}
exports.isValidSecret = isValidSecret;
function encode(object) {
    return (0, ripple_binary_codec_1.encode)(object);
}
exports.encode = encode;
function encodeForSigning(object) {
    return (0, ripple_binary_codec_1.encodeForSigning)(object);
}
exports.encodeForSigning = encodeForSigning;
function encodeForSigningClaim(object) {
    return (0, ripple_binary_codec_1.encodeForSigningClaim)(object);
}
exports.encodeForSigningClaim = encodeForSigningClaim;
function encodeForMultiSigning(object, signer) {
    return (0, ripple_binary_codec_1.encodeForMultisigning)(object, signer);
}
exports.encodeForMultiSigning = encodeForMultiSigning;
function decode(hex) {
    return (0, ripple_binary_codec_1.decode)(hex);
}
exports.decode = decode;
function isValidAddress(address) {
    return (0, ripple_address_codec_1.isValidXAddress)(address) || (0, ripple_address_codec_1.isValidClassicAddress)(address);
}
exports.isValidAddress = isValidAddress;
function hasNextPage(response) {
    return Boolean(response.result['marker']);
}
exports.hasNextPage = hasNextPage;
const hashes = {
    hashSignedTx: hashes_1.hashSignedTx,
    hashTx: hashes_1.hashTx,
    hashAccountRoot: hashes_1.hashAccountRoot,
    hashSignerListId: hashes_1.hashSignerListId,
    hashOfferId: hashes_1.hashOfferId,
    hashTrustline: hashes_1.hashTrustline,
    hashTxTree: hashes_1.hashTxTree,
    hashStateTree: hashes_1.hashStateTree,
    hashLedger: hashes_1.hashLedger,
    hashLedgerHeader: hashes_1.hashLedgerHeader,
    hashEscrow: hashes_1.hashEscrow,
    hashPaymentChannel: hashes_1.hashPaymentChannel,
};
exports.hashes = hashes;


/***/ }),

/***/ "./dist/npm/utils/parseNFTokenID.js":
/*!******************************************!*\
  !*** ./dist/npm/utils/parseNFTokenID.js ***!
  \******************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const ripple_address_codec_1 = __webpack_require__(/*! ripple-address-codec */ "../../node_modules/ripple-address-codec/dist/index.js");
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
function unscrambleTaxon(taxon, tokenSeq) {
    const seed = 384160001;
    const increment = 2459;
    const max = 4294967296;
    const scramble = new bignumber_js_1.default(seed)
        .multipliedBy(tokenSeq)
        .modulo(max)
        .plus(increment)
        .modulo(max)
        .toNumber();
    return (taxon ^ scramble) >>> 0;
}
function parseNFTokenID(nftokenID) {
    const expectedLength = 64;
    if (nftokenID.length !== expectedLength) {
        throw new errors_1.XrplError(`Attempting to parse a nftokenID with length ${nftokenID.length}
    , but expected a token with length ${expectedLength}`);
    }
    const scrambledTaxon = new bignumber_js_1.default(nftokenID.substring(48, 56), 16).toNumber();
    const sequence = new bignumber_js_1.default(nftokenID.substring(56, 64), 16).toNumber();
    const NFTokenIDData = {
        NFTokenID: nftokenID,
        Flags: new bignumber_js_1.default(nftokenID.substring(0, 4), 16).toNumber(),
        TransferFee: new bignumber_js_1.default(nftokenID.substring(4, 8), 16).toNumber(),
        Issuer: (0, ripple_address_codec_1.encodeAccountID)((0, utils_1.hexToBytes)(nftokenID.substring(8, 48))),
        Taxon: unscrambleTaxon(scrambledTaxon, sequence),
        Sequence: sequence,
    };
    return NFTokenIDData;
}
exports["default"] = parseNFTokenID;


/***/ }),

/***/ "./dist/npm/utils/quality.js":
/*!***********************************!*\
  !*** ./dist/npm/utils/quality.js ***!
  \***********************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.percentToQuality = exports.transferRateToDecimal = exports.qualityToDecimal = exports.decimalToQuality = exports.percentToTransferRate = exports.decimalToTransferRate = void 0;
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const BASE_TEN = 10;
const ONE_BILLION = '1000000000';
const TWO_BILLION = '2000000000';
function percentToDecimal(percent) {
    if (!percent.endsWith('%')) {
        throw new errors_1.ValidationError(`Value ${percent} must end with %`);
    }
    const split = percent.split('%').filter((str) => str !== '');
    if (split.length !== 1) {
        throw new errors_1.ValidationError(`Value ${percent} contains too many % signs`);
    }
    return new bignumber_js_1.default(split[0]).dividedBy('100').toString(BASE_TEN);
}
function decimalToTransferRate(decimal) {
    const rate = new bignumber_js_1.default(decimal).times(ONE_BILLION).plus(ONE_BILLION);
    if (rate.isLessThan(ONE_BILLION) || rate.isGreaterThan(TWO_BILLION)) {
        throw new errors_1.ValidationError(`Decimal value must be between 0 and 1.00.`);
    }
    const billionths = rate.toString(BASE_TEN);
    if (billionths === ONE_BILLION) {
        return 0;
    }
    if (billionths === 'NaN') {
        throw new errors_1.ValidationError(`Value is not a number`);
    }
    if (billionths.includes('.')) {
        throw new errors_1.ValidationError(`Decimal exceeds maximum precision.`);
    }
    return Number(billionths);
}
exports.decimalToTransferRate = decimalToTransferRate;
function percentToTransferRate(percent) {
    return decimalToTransferRate(percentToDecimal(percent));
}
exports.percentToTransferRate = percentToTransferRate;
function decimalToQuality(decimal) {
    const rate = new bignumber_js_1.default(decimal).times(ONE_BILLION);
    const billionths = rate.toString(BASE_TEN);
    if (billionths === 'NaN') {
        throw new errors_1.ValidationError(`Value is not a number`);
    }
    if (billionths.includes('-')) {
        throw new errors_1.ValidationError('Cannot have negative Quality');
    }
    if (billionths === ONE_BILLION) {
        return 0;
    }
    if (billionths.includes('.')) {
        throw new errors_1.ValidationError(`Decimal exceeds maximum precision.`);
    }
    return Number(billionths);
}
exports.decimalToQuality = decimalToQuality;
function qualityToDecimal(quality) {
    if (!Number.isInteger(quality)) {
        throw new errors_1.ValidationError('Quality must be an integer');
    }
    if (quality < 0) {
        throw new errors_1.ValidationError('Negative quality not allowed');
    }
    if (quality === 0) {
        return '1';
    }
    const decimal = new bignumber_js_1.default(quality).dividedBy(ONE_BILLION);
    return decimal.toString(BASE_TEN);
}
exports.qualityToDecimal = qualityToDecimal;
function transferRateToDecimal(rate) {
    if (!Number.isInteger(rate)) {
        throw new errors_1.ValidationError('Error decoding, transfer Rate must be an integer');
    }
    if (rate === 0) {
        return '0';
    }
    const decimal = new bignumber_js_1.default(rate).minus(ONE_BILLION).dividedBy(ONE_BILLION);
    if (decimal.isLessThan(0)) {
        throw new errors_1.ValidationError('Error decoding, negative transfer rate');
    }
    return decimal.toString(BASE_TEN);
}
exports.transferRateToDecimal = transferRateToDecimal;
function percentToQuality(percent) {
    return decimalToQuality(percentToDecimal(percent));
}
exports.percentToQuality = percentToQuality;


/***/ }),

/***/ "./dist/npm/utils/signPaymentChannelClaim.js":
/*!***************************************************!*\
  !*** ./dist/npm/utils/signPaymentChannelClaim.js ***!
  \***************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
const xrpConversion_1 = __webpack_require__(/*! ./xrpConversion */ "./dist/npm/utils/xrpConversion.js");
function signPaymentChannelClaim(channel, xrpAmount, privateKey) {
    const signingData = (0, ripple_binary_codec_1.encodeForSigningClaim)({
        channel,
        amount: (0, xrpConversion_1.xrpToDrops)(xrpAmount),
    });
    return (0, ripple_keypairs_1.sign)(signingData, privateKey);
}
exports["default"] = signPaymentChannelClaim;


/***/ }),

/***/ "./dist/npm/utils/stringConversion.js":
/*!********************************************!*\
  !*** ./dist/npm/utils/stringConversion.js ***!
  \********************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.convertStringToHex = exports.convertHexToString = void 0;
const utils_1 = __webpack_require__(/*! @xrplf/isomorphic/utils */ "../../node_modules/@xrplf/isomorphic/dist/utils/browser.js");
function convertStringToHex(string) {
    return (0, utils_1.stringToHex)(string);
}
exports.convertStringToHex = convertStringToHex;
function convertHexToString(hex, encoding = 'utf8') {
    return (0, utils_1.hexToString)(hex, encoding);
}
exports.convertHexToString = convertHexToString;


/***/ }),

/***/ "./dist/npm/utils/timeConversion.js":
/*!******************************************!*\
  !*** ./dist/npm/utils/timeConversion.js ***!
  \******************************************/
/***/ ((__unused_webpack_module, exports) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.isoTimeToRippleTime = exports.rippleTimeToISOTime = exports.unixTimeToRippleTime = exports.rippleTimeToUnixTime = void 0;
const RIPPLE_EPOCH_DIFF = 0x386d4380;
function rippleTimeToUnixTime(rpepoch) {
    return (rpepoch + RIPPLE_EPOCH_DIFF) * 1000;
}
exports.rippleTimeToUnixTime = rippleTimeToUnixTime;
function unixTimeToRippleTime(timestamp) {
    return Math.round(timestamp / 1000) - RIPPLE_EPOCH_DIFF;
}
exports.unixTimeToRippleTime = unixTimeToRippleTime;
function rippleTimeToISOTime(rippleTime) {
    return new Date(rippleTimeToUnixTime(rippleTime)).toISOString();
}
exports.rippleTimeToISOTime = rippleTimeToISOTime;
function isoTimeToRippleTime(iso8601) {
    const isoDate = typeof iso8601 === 'string' ? new Date(iso8601) : iso8601;
    return unixTimeToRippleTime(isoDate.getTime());
}
exports.isoTimeToRippleTime = isoTimeToRippleTime;


/***/ }),

/***/ "./dist/npm/utils/verifyPaymentChannelClaim.js":
/*!*****************************************************!*\
  !*** ./dist/npm/utils/verifyPaymentChannelClaim.js ***!
  \*****************************************************/
/***/ ((__unused_webpack_module, exports, __webpack_require__) => {

"use strict";

Object.defineProperty(exports, "__esModule", ({ value: true }));
const ripple_binary_codec_1 = __webpack_require__(/*! ripple-binary-codec */ "../../node_modules/ripple-binary-codec/dist/index.js");
const ripple_keypairs_1 = __webpack_require__(/*! ripple-keypairs */ "../../node_modules/ripple-keypairs/dist/index.js");
const xrpConversion_1 = __webpack_require__(/*! ./xrpConversion */ "./dist/npm/utils/xrpConversion.js");
function verifyPaymentChannelClaim(channel, xrpAmount, signature, publicKey) {
    const signingData = (0, ripple_binary_codec_1.encodeForSigningClaim)({
        channel,
        amount: (0, xrpConversion_1.xrpToDrops)(xrpAmount),
    });
    return (0, ripple_keypairs_1.verify)(signingData, signature, publicKey);
}
exports["default"] = verifyPaymentChannelClaim;


/***/ }),

/***/ "./dist/npm/utils/xrpConversion.js":
/*!*****************************************!*\
  !*** ./dist/npm/utils/xrpConversion.js ***!
  \*****************************************/
/***/ (function(__unused_webpack_module, exports, __webpack_require__) {

"use strict";

var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", ({ value: true }));
exports.xrpToDrops = exports.dropsToXrp = void 0;
const bignumber_js_1 = __importDefault(__webpack_require__(/*! bignumber.js */ "../../node_modules/bignumber.js/bignumber.js"));
const errors_1 = __webpack_require__(/*! ../errors */ "./dist/npm/errors.js");
const DROPS_PER_XRP = 1000000.0;
const MAX_FRACTION_LENGTH = 6;
const BASE_TEN = 10;
const SANITY_CHECK = /^-?[0-9.]+$/u;
function dropsToXrp(dropsToConvert) {
    const drops = new bignumber_js_1.default(dropsToConvert).toString(BASE_TEN);
    if (typeof dropsToConvert === 'string' && drops === 'NaN') {
        throw new errors_1.ValidationError(`dropsToXrp: invalid value '${dropsToConvert}', should be a BigNumber or string-encoded number.`);
    }
    if (drops.includes('.')) {
        throw new errors_1.ValidationError(`dropsToXrp: value '${drops}' has too many decimal places.`);
    }
    if (!SANITY_CHECK.exec(drops)) {
        throw new errors_1.ValidationError(`dropsToXrp: failed sanity check -` +
            ` value '${drops}',` +
            ` does not match (^-?[0-9]+$).`);
    }
    return new bignumber_js_1.default(drops).dividedBy(DROPS_PER_XRP).toNumber();
}
exports.dropsToXrp = dropsToXrp;
function xrpToDrops(xrpToConvert) {
    const xrp = new bignumber_js_1.default(xrpToConvert).toString(BASE_TEN);
    if (typeof xrpToConvert === 'string' && xrp === 'NaN') {
        throw new errors_1.ValidationError(`xrpToDrops: invalid value '${xrpToConvert}', should be a BigNumber or string-encoded number.`);
    }
    if (!SANITY_CHECK.exec(xrp)) {
        throw new errors_1.ValidationError(`xrpToDrops: failed sanity check - value '${xrp}', does not match (^-?[0-9.]+$).`);
    }
    const components = xrp.split('.');
    if (components.length > 2) {
        throw new errors_1.ValidationError(`xrpToDrops: failed sanity check - value '${xrp}' has too many decimal points.`);
    }
    const fraction = components[1] || '0';
    if (fraction.length > MAX_FRACTION_LENGTH) {
        throw new errors_1.ValidationError(`xrpToDrops: value '${xrp}' has too many decimal places.`);
    }
    return new bignumber_js_1.default(xrp)
        .times(DROPS_PER_XRP)
        .integerValue(bignumber_js_1.default.ROUND_FLOOR)
        .toString(BASE_TEN);
}
exports.xrpToDrops = xrpToDrops;


/***/ }),

/***/ "../../node_modules/ripple-binary-codec/dist/enums/definitions.json":
/*!**************************************************************************!*\
  !*** ../../node_modules/ripple-binary-codec/dist/enums/definitions.json ***!
  \**************************************************************************/
/***/ ((module) => {

"use strict";
module.exports = /*#__PURE__*/JSON.parse('{"FIELDS":[["Generic",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":0,"type":"Unknown"}],["Invalid",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":-1,"type":"Unknown"}],["ObjectEndMarker",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"STObject"}],["ArrayEndMarker",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"STArray"}],["taker_gets_funded",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":258,"type":"Amount"}],["taker_pays_funded",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":259,"type":"Amount"}],["LedgerEntryType",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"UInt16"}],["TransactionType",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"UInt16"}],["SignerWeight",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"UInt16"}],["TransferFee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"UInt16"}],["TradingFee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"UInt16"}],["DiscountedFee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"UInt16"}],["Version",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"UInt16"}],["HookStateChangeCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"UInt16"}],["HookEmitCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"UInt16"}],["HookExecutionIndex",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"UInt16"}],["HookApiVersion",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":20,"type":"UInt16"}],["LedgerFixType",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":21,"type":"UInt16"}],["NetworkID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"UInt32"}],["Flags",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"UInt32"}],["SourceTag",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"UInt32"}],["Sequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"UInt32"}],["PreviousTxnLgrSeq",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"UInt32"}],["LedgerSequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"UInt32"}],["CloseTime",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":7,"type":"UInt32"}],["ParentCloseTime",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":8,"type":"UInt32"}],["SigningTime",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":9,"type":"UInt32"}],["Expiration",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":10,"type":"UInt32"}],["TransferRate",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":11,"type":"UInt32"}],["WalletSize",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":12,"type":"UInt32"}],["OwnerCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":13,"type":"UInt32"}],["DestinationTag",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":14,"type":"UInt32"}],["LastUpdateTime",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":15,"type":"UInt32"}],["HighQualityIn",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"UInt32"}],["HighQualityOut",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"UInt32"}],["LowQualityIn",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"UInt32"}],["LowQualityOut",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"UInt32"}],["QualityIn",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":20,"type":"UInt32"}],["QualityOut",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":21,"type":"UInt32"}],["StampEscrow",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":22,"type":"UInt32"}],["BondAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":23,"type":"UInt32"}],["LoadFee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":24,"type":"UInt32"}],["OfferSequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":25,"type":"UInt32"}],["FirstLedgerSequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":26,"type":"UInt32"}],["LastLedgerSequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":27,"type":"UInt32"}],["TransactionIndex",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":28,"type":"UInt32"}],["OperationLimit",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":29,"type":"UInt32"}],["ReferenceFeeUnits",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":30,"type":"UInt32"}],["ReserveBase",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":31,"type":"UInt32"}],["ReserveIncrement",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":32,"type":"UInt32"}],["SetFlag",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":33,"type":"UInt32"}],["ClearFlag",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":34,"type":"UInt32"}],["SignerQuorum",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":35,"type":"UInt32"}],["CancelAfter",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":36,"type":"UInt32"}],["FinishAfter",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":37,"type":"UInt32"}],["SignerListID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":38,"type":"UInt32"}],["SettleDelay",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":39,"type":"UInt32"}],["TicketCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":40,"type":"UInt32"}],["TicketSequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":41,"type":"UInt32"}],["NFTokenTaxon",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":42,"type":"UInt32"}],["MintedNFTokens",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":43,"type":"UInt32"}],["BurnedNFTokens",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":44,"type":"UInt32"}],["HookStateCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":45,"type":"UInt32"}],["EmitGeneration",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":46,"type":"UInt32"}],["VoteWeight",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":48,"type":"UInt32"}],["FirstNFTokenSequence",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":50,"type":"UInt32"}],["OracleDocumentID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":51,"type":"UInt32"}],["IndexNext",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"UInt64"}],["IndexPrevious",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"UInt64"}],["BookNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"UInt64"}],["OwnerNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"UInt64"}],["BaseFee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"UInt64"}],["ExchangeRate",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"UInt64"}],["LowNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":7,"type":"UInt64"}],["HighNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":8,"type":"UInt64"}],["DestinationNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":9,"type":"UInt64"}],["Cookie",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":10,"type":"UInt64"}],["ServerVersion",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":11,"type":"UInt64"}],["NFTokenOfferNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":12,"type":"UInt64"}],["EmitBurden",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":13,"type":"UInt64"}],["HookOn",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"UInt64"}],["HookInstructionCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"UInt64"}],["HookReturnCode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"UInt64"}],["ReferenceCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"UInt64"}],["XChainClaimID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":20,"type":"UInt64"}],["XChainAccountCreateCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":21,"type":"UInt64"}],["XChainAccountClaimCount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":22,"type":"UInt64"}],["AssetPrice",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":23,"type":"UInt64"}],["MaximumAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":24,"type":"UInt64"}],["OutstandingAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":25,"type":"UInt64"}],["MPTAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":26,"type":"UInt64"}],["IssuerNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":27,"type":"UInt64"}],["SubjectNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":28,"type":"UInt64"}],["EmailHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Hash128"}],["LedgerHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Hash256"}],["ParentHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"Hash256"}],["TransactionHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"Hash256"}],["AccountHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"Hash256"}],["PreviousTxnID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"Hash256"}],["LedgerIndex",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"Hash256"}],["WalletLocator",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":7,"type":"Hash256"}],["RootIndex",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":8,"type":"Hash256"}],["AccountTxnID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":9,"type":"Hash256"}],["NFTokenID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":10,"type":"Hash256"}],["EmitParentTxnID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":11,"type":"Hash256"}],["EmitNonce",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":12,"type":"Hash256"}],["EmitHookHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":13,"type":"Hash256"}],["AMMID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":14,"type":"Hash256"}],["BookDirectory",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"Hash256"}],["InvoiceID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"Hash256"}],["Nickname",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"Hash256"}],["Amendment",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"Hash256"}],["Digest",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":21,"type":"Hash256"}],["Channel",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":22,"type":"Hash256"}],["ConsensusHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":23,"type":"Hash256"}],["CheckID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":24,"type":"Hash256"}],["ValidatedHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":25,"type":"Hash256"}],["PreviousPageMin",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":26,"type":"Hash256"}],["NextPageMin",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":27,"type":"Hash256"}],["NFTokenBuyOffer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":28,"type":"Hash256"}],["NFTokenSellOffer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":29,"type":"Hash256"}],["HookStateKey",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":30,"type":"Hash256"}],["HookHash",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":31,"type":"Hash256"}],["HookNamespace",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":32,"type":"Hash256"}],["HookSetTxnID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":33,"type":"Hash256"}],["DomainID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":34,"type":"Hash256"}],["hash",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":257,"type":"Hash256"}],["index",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":258,"type":"Hash256"}],["Amount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Amount"}],["Balance",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"Amount"}],["LimitAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"Amount"}],["TakerPays",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"Amount"}],["TakerGets",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"Amount"}],["LowLimit",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"Amount"}],["HighLimit",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":7,"type":"Amount"}],["Fee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":8,"type":"Amount"}],["SendMax",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":9,"type":"Amount"}],["DeliverMin",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":10,"type":"Amount"}],["Amount2",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":11,"type":"Amount"}],["BidMin",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":12,"type":"Amount"}],["BidMax",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":13,"type":"Amount"}],["MinimumOffer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"Amount"}],["RippleEscrow",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"Amount"}],["DeliveredAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"Amount"}],["NFTokenBrokerFee",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"Amount"}],["BaseFeeDrops",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":22,"type":"Amount"}],["ReserveBaseDrops",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":23,"type":"Amount"}],["ReserveIncrementDrops",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":24,"type":"Amount"}],["LPTokenOut",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":25,"type":"Amount"}],["LPTokenIn",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":26,"type":"Amount"}],["EPrice",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":27,"type":"Amount"}],["Price",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":28,"type":"Amount"}],["SignatureReward",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":29,"type":"Amount"}],["MinAccountCreateAmount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":30,"type":"Amount"}],["LPTokenBalance",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":31,"type":"Amount"}],["PublicKey",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":1,"type":"Blob"}],["MessageKey",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":2,"type":"Blob"}],["SigningPubKey",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":3,"type":"Blob"}],["TxnSignature",{"isSerialized":true,"isSigningField":false,"isVLEncoded":true,"nth":4,"type":"Blob"}],["URI",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":5,"type":"Blob"}],["Signature",{"isSerialized":true,"isSigningField":false,"isVLEncoded":true,"nth":6,"type":"Blob"}],["Domain",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":7,"type":"Blob"}],["FundCode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":8,"type":"Blob"}],["RemoveCode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":9,"type":"Blob"}],["ExpireCode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":10,"type":"Blob"}],["CreateCode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":11,"type":"Blob"}],["MemoType",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":12,"type":"Blob"}],["MemoData",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":13,"type":"Blob"}],["MemoFormat",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":14,"type":"Blob"}],["Fulfillment",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":16,"type":"Blob"}],["Condition",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":17,"type":"Blob"}],["MasterSignature",{"isSerialized":true,"isSigningField":false,"isVLEncoded":true,"nth":18,"type":"Blob"}],["UNLModifyValidator",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":19,"type":"Blob"}],["ValidatorToDisable",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":20,"type":"Blob"}],["ValidatorToReEnable",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":21,"type":"Blob"}],["HookStateData",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":22,"type":"Blob"}],["HookReturnString",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":23,"type":"Blob"}],["HookParameterName",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":24,"type":"Blob"}],["HookParameterValue",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":25,"type":"Blob"}],["DIDDocument",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":26,"type":"Blob"}],["Data",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":27,"type":"Blob"}],["AssetClass",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":28,"type":"Blob"}],["Provider",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":29,"type":"Blob"}],["MPTokenMetadata",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":30,"type":"Blob"}],["CredentialType",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":31,"type":"Blob"}],["Account",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":1,"type":"AccountID"}],["Owner",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":2,"type":"AccountID"}],["Destination",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":3,"type":"AccountID"}],["Issuer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":4,"type":"AccountID"}],["Authorize",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":5,"type":"AccountID"}],["Unauthorize",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":6,"type":"AccountID"}],["RegularKey",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":8,"type":"AccountID"}],["NFTokenMinter",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":9,"type":"AccountID"}],["EmitCallback",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":10,"type":"AccountID"}],["Holder",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":11,"type":"AccountID"}],["HookAccount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":16,"type":"AccountID"}],["OtherChainSource",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":18,"type":"AccountID"}],["OtherChainDestination",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":19,"type":"AccountID"}],["AttestationSignerAccount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":20,"type":"AccountID"}],["AttestationRewardAccount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":21,"type":"AccountID"}],["LockingChainDoor",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":22,"type":"AccountID"}],["IssuingChainDoor",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":23,"type":"AccountID"}],["Subject",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":24,"type":"AccountID"}],["Number",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Number"}],["TransactionMetaData",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"STObject"}],["CreatedNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"STObject"}],["DeletedNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"STObject"}],["ModifiedNode",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"STObject"}],["PreviousFields",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"STObject"}],["FinalFields",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":7,"type":"STObject"}],["NewFields",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":8,"type":"STObject"}],["TemplateEntry",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":9,"type":"STObject"}],["Memo",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":10,"type":"STObject"}],["SignerEntry",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":11,"type":"STObject"}],["NFToken",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":12,"type":"STObject"}],["EmitDetails",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":13,"type":"STObject"}],["Hook",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":14,"type":"STObject"}],["Signer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"STObject"}],["Majority",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"STObject"}],["DisabledValidator",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"STObject"}],["EmittedTxn",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":20,"type":"STObject"}],["HookExecution",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":21,"type":"STObject"}],["HookDefinition",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":22,"type":"STObject"}],["HookParameter",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":23,"type":"STObject"}],["HookGrant",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":24,"type":"STObject"}],["VoteEntry",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":25,"type":"STObject"}],["AuctionSlot",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":26,"type":"STObject"}],["AuthAccount",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":27,"type":"STObject"}],["XChainClaimProofSig",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":28,"type":"STObject"}],["XChainCreateAccountProofSig",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":29,"type":"STObject"}],["XChainClaimAttestationCollectionElement",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":30,"type":"STObject"}],["XChainCreateAccountAttestationCollectionElement",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":31,"type":"STObject"}],["PriceData",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":32,"type":"STObject"}],["Credential",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":33,"type":"STObject"}],["Signers",{"isSerialized":true,"isSigningField":false,"isVLEncoded":false,"nth":3,"type":"STArray"}],["SignerEntries",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"STArray"}],["Template",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"STArray"}],["Necessary",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":6,"type":"STArray"}],["Sufficient",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":7,"type":"STArray"}],["AffectedNodes",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":8,"type":"STArray"}],["Memos",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":9,"type":"STArray"}],["NFTokens",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":10,"type":"STArray"}],["Hooks",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":11,"type":"STArray"}],["VoteSlots",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":12,"type":"STArray"}],["Majorities",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"STArray"}],["DisabledValidators",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"STArray"}],["HookExecutions",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"STArray"}],["HookParameters",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"STArray"}],["HookGrants",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":20,"type":"STArray"}],["XChainClaimAttestations",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":21,"type":"STArray"}],["XChainCreateAccountAttestations",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":22,"type":"STArray"}],["PriceDataSeries",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":24,"type":"STArray"}],["AuthAccounts",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":25,"type":"STArray"}],["AuthorizeCredentials",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":26,"type":"STArray"}],["UnauthorizeCredentials",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":27,"type":"STArray"}],["AcceptedCredentials",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":28,"type":"STArray"}],["CloseResolution",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"UInt8"}],["Method",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"UInt8"}],["TransactionResult",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"UInt8"}],["Scale",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"UInt8"}],["AssetScale",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":5,"type":"UInt8"}],["TickSize",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":16,"type":"UInt8"}],["UNLModifyDisabling",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":17,"type":"UInt8"}],["HookResult",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":18,"type":"UInt8"}],["WasLockingChainSend",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":19,"type":"UInt8"}],["TakerPaysCurrency",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Hash160"}],["TakerPaysIssuer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"Hash160"}],["TakerGetsCurrency",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"Hash160"}],["TakerGetsIssuer",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"Hash160"}],["Paths",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"PathSet"}],["Indexes",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":1,"type":"Vector256"}],["Hashes",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":2,"type":"Vector256"}],["Amendments",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":3,"type":"Vector256"}],["NFTokenOffers",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":4,"type":"Vector256"}],["CredentialIDs",{"isSerialized":true,"isSigningField":true,"isVLEncoded":true,"nth":5,"type":"Vector256"}],["MPTokenIssuanceID",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Hash192"}],["LockingChainIssue",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Issue"}],["IssuingChainIssue",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"Issue"}],["Asset",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":3,"type":"Issue"}],["Asset2",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":4,"type":"Issue"}],["XChainBridge",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"XChainBridge"}],["BaseAsset",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":1,"type":"Currency"}],["QuoteAsset",{"isSerialized":true,"isSigningField":true,"isVLEncoded":false,"nth":2,"type":"Currency"}],["Transaction",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":257,"type":"Transaction"}],["LedgerEntry",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":257,"type":"LedgerEntry"}],["Validation",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":257,"type":"Validation"}],["Metadata",{"isSerialized":false,"isSigningField":false,"isVLEncoded":false,"nth":257,"type":"Metadata"}]],"LEDGER_ENTRY_TYPES":{"AMM":121,"AccountRoot":97,"Amendments":102,"Bridge":105,"Check":67,"Credential":129,"DID":73,"DepositPreauth":112,"DirectoryNode":100,"Escrow":117,"FeeSettings":115,"Invalid":-1,"LedgerHashes":104,"MPToken":127,"MPTokenIssuance":126,"NFTokenOffer":55,"NFTokenPage":80,"NegativeUNL":78,"Offer":111,"Oracle":128,"PayChannel":120,"PermissionedDomain":130,"RippleState":114,"SignerList":83,"Ticket":84,"XChainOwnedClaimID":113,"XChainOwnedCreateAccountClaimID":116},"TRANSACTION_RESULTS":{"tecAMM_ACCOUNT":168,"tecAMM_BALANCE":163,"tecAMM_EMPTY":166,"tecAMM_FAILED":164,"tecAMM_INVALID_TOKENS":165,"tecAMM_NOT_EMPTY":167,"tecARRAY_EMPTY":190,"tecARRAY_TOO_LARGE":191,"tecBAD_CREDENTIALS":193,"tecCANT_ACCEPT_OWN_NFTOKEN_OFFER":158,"tecCLAIM":100,"tecCRYPTOCONDITION_ERROR":146,"tecDIR_FULL":121,"tecDST_TAG_NEEDED":143,"tecDUPLICATE":149,"tecEMPTY_DID":187,"tecEXPIRED":148,"tecFAILED_PROCESSING":105,"tecFROZEN":137,"tecHAS_OBLIGATIONS":151,"tecHOOK_REJECTED":153,"tecINCOMPLETE":169,"tecINSUFFICIENT_FUNDS":159,"tecINSUFFICIENT_PAYMENT":161,"tecINSUFFICIENT_RESERVE":141,"tecINSUFF_FEE":136,"tecINSUF_RESERVE_LINE":122,"tecINSUF_RESERVE_OFFER":123,"tecINTERNAL":144,"tecINVALID_UPDATE_TIME":188,"tecINVARIANT_FAILED":147,"tecKILLED":150,"tecLOCKED":192,"tecMAX_SEQUENCE_REACHED":154,"tecNEED_MASTER_KEY":142,"tecNFTOKEN_BUY_SELL_MISMATCH":156,"tecNFTOKEN_OFFER_TYPE_MISMATCH":157,"tecNO_ALTERNATIVE_KEY":130,"tecNO_AUTH":134,"tecNO_DST":124,"tecNO_DST_INSUF_XRP":125,"tecNO_ENTRY":140,"tecNO_ISSUER":133,"tecNO_LINE":135,"tecNO_LINE_INSUF_RESERVE":126,"tecNO_LINE_REDUNDANT":127,"tecNO_PERMISSION":139,"tecNO_REGULAR_KEY":131,"tecNO_SUITABLE_NFTOKEN_PAGE":155,"tecNO_TARGET":138,"tecOBJECT_NOT_FOUND":160,"tecOVERSIZE":145,"tecOWNERS":132,"tecPATH_DRY":128,"tecPATH_PARTIAL":101,"tecTOKEN_PAIR_NOT_FOUND":189,"tecTOO_SOON":152,"tecUNFUNDED":129,"tecUNFUNDED_ADD":102,"tecUNFUNDED_AMM":162,"tecUNFUNDED_OFFER":103,"tecUNFUNDED_PAYMENT":104,"tecXCHAIN_ACCOUNT_CREATE_PAST":181,"tecXCHAIN_ACCOUNT_CREATE_TOO_MANY":182,"tecXCHAIN_BAD_CLAIM_ID":172,"tecXCHAIN_BAD_PUBLIC_KEY_ACCOUNT_PAIR":185,"tecXCHAIN_BAD_TRANSFER_ISSUE":170,"tecXCHAIN_CLAIM_NO_QUORUM":173,"tecXCHAIN_CREATE_ACCOUNT_DISABLED":186,"tecXCHAIN_CREATE_ACCOUNT_NONXRP_ISSUE":175,"tecXCHAIN_INSUFF_CREATE_AMOUNT":180,"tecXCHAIN_NO_CLAIM_ID":171,"tecXCHAIN_NO_SIGNERS_LIST":178,"tecXCHAIN_PAYMENT_FAILED":183,"tecXCHAIN_PROOF_UNKNOWN_KEY":174,"tecXCHAIN_REWARD_MISMATCH":177,"tecXCHAIN_SELF_COMMIT":184,"tecXCHAIN_SENDING_ACCOUNT_MISMATCH":179,"tecXCHAIN_WRONG_CHAIN":176,"tefALREADY":-198,"tefBAD_ADD_AUTH":-197,"tefBAD_AUTH":-196,"tefBAD_AUTH_MASTER":-183,"tefBAD_LEDGER":-195,"tefBAD_QUORUM":-185,"tefBAD_SIGNATURE":-186,"tefCREATED":-194,"tefEXCEPTION":-193,"tefFAILURE":-199,"tefINTERNAL":-192,"tefINVALID_LEDGER_FIX_TYPE":-178,"tefINVARIANT_FAILED":-182,"tefMASTER_DISABLED":-188,"tefMAX_LEDGER":-187,"tefNFTOKEN_IS_NOT_TRANSFERABLE":-179,"tefNOT_MULTI_SIGNING":-184,"tefNO_AUTH_REQUIRED":-191,"tefNO_TICKET":-180,"tefPAST_SEQ":-190,"tefTOO_BIG":-181,"tefWRONG_PRIOR":-189,"telBAD_DOMAIN":-398,"telBAD_PATH_COUNT":-397,"telBAD_PUBLIC_KEY":-396,"telCAN_NOT_QUEUE":-392,"telCAN_NOT_QUEUE_BALANCE":-391,"telCAN_NOT_QUEUE_BLOCKED":-389,"telCAN_NOT_QUEUE_BLOCKS":-390,"telCAN_NOT_QUEUE_FEE":-388,"telCAN_NOT_QUEUE_FULL":-387,"telENV_RPC_FAILED":-383,"telFAILED_PROCESSING":-395,"telINSUF_FEE_P":-394,"telLOCAL_ERROR":-399,"telNETWORK_ID_MAKES_TX_NON_CANONICAL":-384,"telNO_DST_PARTIAL":-393,"telREQUIRES_NETWORK_ID":-385,"telWRONG_NETWORK":-386,"temARRAY_EMPTY":-253,"temARRAY_TOO_LARGE":-252,"temBAD_AMM_TOKENS":-261,"temBAD_AMOUNT":-298,"temBAD_CURRENCY":-297,"temBAD_EXPIRATION":-296,"temBAD_FEE":-295,"temBAD_ISSUER":-294,"temBAD_LIMIT":-293,"temBAD_NFTOKEN_TRANSFER_FEE":-262,"temBAD_OFFER":-292,"temBAD_PATH":-291,"temBAD_PATH_LOOP":-290,"temBAD_QUORUM":-271,"temBAD_REGKEY":-289,"temBAD_SEND_XRP_LIMIT":-288,"temBAD_SEND_XRP_MAX":-287,"temBAD_SEND_XRP_NO_DIRECT":-286,"temBAD_SEND_XRP_PARTIAL":-285,"temBAD_SEND_XRP_PATHS":-284,"temBAD_SEQUENCE":-283,"temBAD_SIGNATURE":-282,"temBAD_SIGNER":-272,"temBAD_SRC_ACCOUNT":-281,"temBAD_TICK_SIZE":-269,"temBAD_TRANSFER_FEE":-251,"temBAD_TRANSFER_RATE":-280,"temBAD_WEIGHT":-270,"temCANNOT_PREAUTH_SELF":-267,"temDISABLED":-273,"temDST_IS_SRC":-279,"temDST_NEEDED":-278,"temEMPTY_DID":-254,"temINVALID":-277,"temINVALID_ACCOUNT_ID":-268,"temINVALID_COUNT":-266,"temINVALID_FLAG":-276,"temMALFORMED":-299,"temREDUNDANT":-275,"temRIPPLE_EMPTY":-274,"temSEQ_AND_TICKET":-263,"temUNCERTAIN":-265,"temUNKNOWN":-264,"temXCHAIN_BAD_PROOF":-259,"temXCHAIN_BRIDGE_BAD_ISSUES":-258,"temXCHAIN_BRIDGE_BAD_MIN_ACCOUNT_CREATE_AMOUNT":-256,"temXCHAIN_BRIDGE_BAD_REWARD_AMOUNT":-255,"temXCHAIN_BRIDGE_NONDOOR_OWNER":-257,"temXCHAIN_EQUAL_DOOR_ACCOUNTS":-260,"terFUNDS_SPENT":-98,"terINSUF_FEE_B":-97,"terLAST":-91,"terNO_ACCOUNT":-96,"terNO_AMM":-87,"terNO_AUTH":-95,"terNO_LINE":-94,"terNO_RIPPLE":-90,"terOWNERS":-93,"terPRE_SEQ":-92,"terPRE_TICKET":-88,"terQUEUED":-89,"terRETRY":-99,"tesSUCCESS":0},"TRANSACTION_TYPES":{"AMMBid":39,"AMMClawback":31,"AMMCreate":35,"AMMDelete":40,"AMMDeposit":36,"AMMVote":38,"AMMWithdraw":37,"AccountDelete":21,"AccountSet":3,"CheckCancel":18,"CheckCash":17,"CheckCreate":16,"Clawback":30,"CredentialAccept":59,"CredentialCreate":58,"CredentialDelete":60,"DIDDelete":50,"DIDSet":49,"DepositPreauth":19,"EnableAmendment":100,"EscrowCancel":4,"EscrowCreate":1,"EscrowFinish":2,"Invalid":-1,"LedgerStateFix":53,"MPTokenAuthorize":57,"MPTokenIssuanceCreate":54,"MPTokenIssuanceDestroy":55,"MPTokenIssuanceSet":56,"NFTokenAcceptOffer":29,"NFTokenBurn":26,"NFTokenCancelOffer":28,"NFTokenCreateOffer":27,"NFTokenMint":25,"NFTokenModify":61,"OfferCancel":8,"OfferCreate":7,"OracleDelete":52,"OracleSet":51,"Payment":0,"PaymentChannelClaim":15,"PaymentChannelCreate":13,"PaymentChannelFund":14,"PermissionedDomainDelete":63,"PermissionedDomainSet":62,"SetFee":101,"SetRegularKey":5,"SignerListSet":12,"TicketCreate":10,"TrustSet":20,"UNLModify":102,"XChainAccountCreateCommit":44,"XChainAddAccountCreateAttestation":46,"XChainAddClaimAttestation":45,"XChainClaim":43,"XChainCommit":42,"XChainCreateBridge":48,"XChainCreateClaimID":41,"XChainModifyBridge":47},"TYPES":{"AccountID":8,"Amount":6,"Blob":7,"Currency":26,"Done":-1,"Hash128":4,"Hash160":17,"Hash192":21,"Hash256":5,"Issue":24,"LedgerEntry":10002,"Metadata":10004,"NotPresent":0,"Number":9,"PathSet":18,"STArray":15,"STObject":14,"Transaction":10001,"UInt16":1,"UInt32":2,"UInt384":22,"UInt512":23,"UInt64":3,"UInt8":16,"UInt96":20,"Unknown":-2,"Validation":10003,"Vector256":19,"XChainBridge":25}}');

/***/ }),

/***/ "./dist/npm/Wallet/rfc1751Words.json":
/*!*******************************************!*\
  !*** ./dist/npm/Wallet/rfc1751Words.json ***!
  \*******************************************/
/***/ ((module) => {

"use strict";
module.exports = /*#__PURE__*/JSON.parse('["A","ABE","ACE","ACT","AD","ADA","ADD","AGO","AID","AIM","AIR","ALL","ALP","AM","AMY","AN","ANA","AND","ANN","ANT","ANY","APE","APS","APT","ARC","ARE","ARK","ARM","ART","AS","ASH","ASK","AT","ATE","AUG","AUK","AVE","AWE","AWK","AWL","AWN","AX","AYE","BAD","BAG","BAH","BAM","BAN","BAR","BAT","BAY","BE","BED","BEE","BEG","BEN","BET","BEY","BIB","BID","BIG","BIN","BIT","BOB","BOG","BON","BOO","BOP","BOW","BOY","BUB","BUD","BUG","BUM","BUN","BUS","BUT","BUY","BY","BYE","CAB","CAL","CAM","CAN","CAP","CAR","CAT","CAW","COD","COG","COL","CON","COO","COP","COT","COW","COY","CRY","CUB","CUE","CUP","CUR","CUT","DAB","DAD","DAM","DAN","DAR","DAY","DEE","DEL","DEN","DES","DEW","DID","DIE","DIG","DIN","DIP","DO","DOE","DOG","DON","DOT","DOW","DRY","DUB","DUD","DUE","DUG","DUN","EAR","EAT","ED","EEL","EGG","EGO","ELI","ELK","ELM","ELY","EM","END","EST","ETC","EVA","EVE","EWE","EYE","FAD","FAN","FAR","FAT","FAY","FED","FEE","FEW","FIB","FIG","FIN","FIR","FIT","FLO","FLY","FOE","FOG","FOR","FRY","FUM","FUN","FUR","GAB","GAD","GAG","GAL","GAM","GAP","GAS","GAY","GEE","GEL","GEM","GET","GIG","GIL","GIN","GO","GOT","GUM","GUN","GUS","GUT","GUY","GYM","GYP","HA","HAD","HAL","HAM","HAN","HAP","HAS","HAT","HAW","HAY","HE","HEM","HEN","HER","HEW","HEY","HI","HID","HIM","HIP","HIS","HIT","HO","HOB","HOC","HOE","HOG","HOP","HOT","HOW","HUB","HUE","HUG","HUH","HUM","HUT","I","ICY","IDA","IF","IKE","ILL","INK","INN","IO","ION","IQ","IRA","IRE","IRK","IS","IT","ITS","IVY","JAB","JAG","JAM","JAN","JAR","JAW","JAY","JET","JIG","JIM","JO","JOB","JOE","JOG","JOT","JOY","JUG","JUT","KAY","KEG","KEN","KEY","KID","KIM","KIN","KIT","LA","LAB","LAC","LAD","LAG","LAM","LAP","LAW","LAY","LEA","LED","LEE","LEG","LEN","LEO","LET","LEW","LID","LIE","LIN","LIP","LIT","LO","LOB","LOG","LOP","LOS","LOT","LOU","LOW","LOY","LUG","LYE","MA","MAC","MAD","MAE","MAN","MAO","MAP","MAT","MAW","MAY","ME","MEG","MEL","MEN","MET","MEW","MID","MIN","MIT","MOB","MOD","MOE","MOO","MOP","MOS","MOT","MOW","MUD","MUG","MUM","MY","NAB","NAG","NAN","NAP","NAT","NAY","NE","NED","NEE","NET","NEW","NIB","NIL","NIP","NIT","NO","NOB","NOD","NON","NOR","NOT","NOV","NOW","NU","NUN","NUT","O","OAF","OAK","OAR","OAT","ODD","ODE","OF","OFF","OFT","OH","OIL","OK","OLD","ON","ONE","OR","ORB","ORE","ORR","OS","OTT","OUR","OUT","OVA","OW","OWE","OWL","OWN","OX","PA","PAD","PAL","PAM","PAN","PAP","PAR","PAT","PAW","PAY","PEA","PEG","PEN","PEP","PER","PET","PEW","PHI","PI","PIE","PIN","PIT","PLY","PO","POD","POE","POP","POT","POW","PRO","PRY","PUB","PUG","PUN","PUP","PUT","QUO","RAG","RAM","RAN","RAP","RAT","RAW","RAY","REB","RED","REP","RET","RIB","RID","RIG","RIM","RIO","RIP","ROB","ROD","ROE","RON","ROT","ROW","ROY","RUB","RUE","RUG","RUM","RUN","RYE","SAC","SAD","SAG","SAL","SAM","SAN","SAP","SAT","SAW","SAY","SEA","SEC","SEE","SEN","SET","SEW","SHE","SHY","SIN","SIP","SIR","SIS","SIT","SKI","SKY","SLY","SO","SOB","SOD","SON","SOP","SOW","SOY","SPA","SPY","SUB","SUD","SUE","SUM","SUN","SUP","TAB","TAD","TAG","TAN","TAP","TAR","TEA","TED","TEE","TEN","THE","THY","TIC","TIE","TIM","TIN","TIP","TO","TOE","TOG","TOM","TON","TOO","TOP","TOW","TOY","TRY","TUB","TUG","TUM","TUN","TWO","UN","UP","US","USE","VAN","VAT","VET","VIE","WAD","WAG","WAR","WAS","WAY","WE","WEB","WED","WEE","WET","WHO","WHY","WIN","WIT","WOK","WON","WOO","WOW","WRY","WU","YAM","YAP","YAW","YE","YEA","YES","YET","YOU","ABED","ABEL","ABET","ABLE","ABUT","ACHE","ACID","ACME","ACRE","ACTA","ACTS","ADAM","ADDS","ADEN","AFAR","AFRO","AGEE","AHEM","AHOY","AIDA","AIDE","AIDS","AIRY","AJAR","AKIN","ALAN","ALEC","ALGA","ALIA","ALLY","ALMA","ALOE","ALSO","ALTO","ALUM","ALVA","AMEN","AMES","AMID","AMMO","AMOK","AMOS","AMRA","ANDY","ANEW","ANNA","ANNE","ANTE","ANTI","AQUA","ARAB","ARCH","AREA","ARGO","ARID","ARMY","ARTS","ARTY","ASIA","ASKS","ATOM","AUNT","AURA","AUTO","AVER","AVID","AVIS","AVON","AVOW","AWAY","AWRY","BABE","BABY","BACH","BACK","BADE","BAIL","BAIT","BAKE","BALD","BALE","BALI","BALK","BALL","BALM","BAND","BANE","BANG","BANK","BARB","BARD","BARE","BARK","BARN","BARR","BASE","BASH","BASK","BASS","BATE","BATH","BAWD","BAWL","BEAD","BEAK","BEAM","BEAN","BEAR","BEAT","BEAU","BECK","BEEF","BEEN","BEER","BEET","BELA","BELL","BELT","BEND","BENT","BERG","BERN","BERT","BESS","BEST","BETA","BETH","BHOY","BIAS","BIDE","BIEN","BILE","BILK","BILL","BIND","BING","BIRD","BITE","BITS","BLAB","BLAT","BLED","BLEW","BLOB","BLOC","BLOT","BLOW","BLUE","BLUM","BLUR","BOAR","BOAT","BOCA","BOCK","BODE","BODY","BOGY","BOHR","BOIL","BOLD","BOLO","BOLT","BOMB","BONA","BOND","BONE","BONG","BONN","BONY","BOOK","BOOM","BOON","BOOT","BORE","BORG","BORN","BOSE","BOSS","BOTH","BOUT","BOWL","BOYD","BRAD","BRAE","BRAG","BRAN","BRAY","BRED","BREW","BRIG","BRIM","BROW","BUCK","BUDD","BUFF","BULB","BULK","BULL","BUNK","BUNT","BUOY","BURG","BURL","BURN","BURR","BURT","BURY","BUSH","BUSS","BUST","BUSY","BYTE","CADY","CAFE","CAGE","CAIN","CAKE","CALF","CALL","CALM","CAME","CANE","CANT","CARD","CARE","CARL","CARR","CART","CASE","CASH","CASK","CAST","CAVE","CEIL","CELL","CENT","CERN","CHAD","CHAR","CHAT","CHAW","CHEF","CHEN","CHEW","CHIC","CHIN","CHOU","CHOW","CHUB","CHUG","CHUM","CITE","CITY","CLAD","CLAM","CLAN","CLAW","CLAY","CLOD","CLOG","CLOT","CLUB","CLUE","COAL","COAT","COCA","COCK","COCO","CODA","CODE","CODY","COED","COIL","COIN","COKE","COLA","COLD","COLT","COMA","COMB","COME","COOK","COOL","COON","COOT","CORD","CORE","CORK","CORN","COST","COVE","COWL","CRAB","CRAG","CRAM","CRAY","CREW","CRIB","CROW","CRUD","CUBA","CUBE","CUFF","CULL","CULT","CUNY","CURB","CURD","CURE","CURL","CURT","CUTS","DADE","DALE","DAME","DANA","DANE","DANG","DANK","DARE","DARK","DARN","DART","DASH","DATA","DATE","DAVE","DAVY","DAWN","DAYS","DEAD","DEAF","DEAL","DEAN","DEAR","DEBT","DECK","DEED","DEEM","DEER","DEFT","DEFY","DELL","DENT","DENY","DESK","DIAL","DICE","DIED","DIET","DIME","DINE","DING","DINT","DIRE","DIRT","DISC","DISH","DISK","DIVE","DOCK","DOES","DOLE","DOLL","DOLT","DOME","DONE","DOOM","DOOR","DORA","DOSE","DOTE","DOUG","DOUR","DOVE","DOWN","DRAB","DRAG","DRAM","DRAW","DREW","DRUB","DRUG","DRUM","DUAL","DUCK","DUCT","DUEL","DUET","DUKE","DULL","DUMB","DUNE","DUNK","DUSK","DUST","DUTY","EACH","EARL","EARN","EASE","EAST","EASY","EBEN","ECHO","EDDY","EDEN","EDGE","EDGY","EDIT","EDNA","EGAN","ELAN","ELBA","ELLA","ELSE","EMIL","EMIT","EMMA","ENDS","ERIC","EROS","EVEN","EVER","EVIL","EYED","FACE","FACT","FADE","FAIL","FAIN","FAIR","FAKE","FALL","FAME","FANG","FARM","FAST","FATE","FAWN","FEAR","FEAT","FEED","FEEL","FEET","FELL","FELT","FEND","FERN","FEST","FEUD","FIEF","FIGS","FILE","FILL","FILM","FIND","FINE","FINK","FIRE","FIRM","FISH","FISK","FIST","FITS","FIVE","FLAG","FLAK","FLAM","FLAT","FLAW","FLEA","FLED","FLEW","FLIT","FLOC","FLOG","FLOW","FLUB","FLUE","FOAL","FOAM","FOGY","FOIL","FOLD","FOLK","FOND","FONT","FOOD","FOOL","FOOT","FORD","FORE","FORK","FORM","FORT","FOSS","FOUL","FOUR","FOWL","FRAU","FRAY","FRED","FREE","FRET","FREY","FROG","FROM","FUEL","FULL","FUME","FUND","FUNK","FURY","FUSE","FUSS","GAFF","GAGE","GAIL","GAIN","GAIT","GALA","GALE","GALL","GALT","GAME","GANG","GARB","GARY","GASH","GATE","GAUL","GAUR","GAVE","GAWK","GEAR","GELD","GENE","GENT","GERM","GETS","GIBE","GIFT","GILD","GILL","GILT","GINA","GIRD","GIRL","GIST","GIVE","GLAD","GLEE","GLEN","GLIB","GLOB","GLOM","GLOW","GLUE","GLUM","GLUT","GOAD","GOAL","GOAT","GOER","GOES","GOLD","GOLF","GONE","GONG","GOOD","GOOF","GORE","GORY","GOSH","GOUT","GOWN","GRAB","GRAD","GRAY","GREG","GREW","GREY","GRID","GRIM","GRIN","GRIT","GROW","GRUB","GULF","GULL","GUNK","GURU","GUSH","GUST","GWEN","GWYN","HAAG","HAAS","HACK","HAIL","HAIR","HALE","HALF","HALL","HALO","HALT","HAND","HANG","HANK","HANS","HARD","HARK","HARM","HART","HASH","HAST","HATE","HATH","HAUL","HAVE","HAWK","HAYS","HEAD","HEAL","HEAR","HEAT","HEBE","HECK","HEED","HEEL","HEFT","HELD","HELL","HELM","HERB","HERD","HERE","HERO","HERS","HESS","HEWN","HICK","HIDE","HIGH","HIKE","HILL","HILT","HIND","HINT","HIRE","HISS","HIVE","HOBO","HOCK","HOFF","HOLD","HOLE","HOLM","HOLT","HOME","HONE","HONK","HOOD","HOOF","HOOK","HOOT","HORN","HOSE","HOST","HOUR","HOVE","HOWE","HOWL","HOYT","HUCK","HUED","HUFF","HUGE","HUGH","HUGO","HULK","HULL","HUNK","HUNT","HURD","HURL","HURT","HUSH","HYDE","HYMN","IBIS","ICON","IDEA","IDLE","IFFY","INCA","INCH","INTO","IONS","IOTA","IOWA","IRIS","IRMA","IRON","ISLE","ITCH","ITEM","IVAN","JACK","JADE","JAIL","JAKE","JANE","JAVA","JEAN","JEFF","JERK","JESS","JEST","JIBE","JILL","JILT","JIVE","JOAN","JOBS","JOCK","JOEL","JOEY","JOHN","JOIN","JOKE","JOLT","JOVE","JUDD","JUDE","JUDO","JUDY","JUJU","JUKE","JULY","JUNE","JUNK","JUNO","JURY","JUST","JUTE","KAHN","KALE","KANE","KANT","KARL","KATE","KEEL","KEEN","KENO","KENT","KERN","KERR","KEYS","KICK","KILL","KIND","KING","KIRK","KISS","KITE","KLAN","KNEE","KNEW","KNIT","KNOB","KNOT","KNOW","KOCH","KONG","KUDO","KURD","KURT","KYLE","LACE","LACK","LACY","LADY","LAID","LAIN","LAIR","LAKE","LAMB","LAME","LAND","LANE","LANG","LARD","LARK","LASS","LAST","LATE","LAUD","LAVA","LAWN","LAWS","LAYS","LEAD","LEAF","LEAK","LEAN","LEAR","LEEK","LEER","LEFT","LEND","LENS","LENT","LEON","LESK","LESS","LEST","LETS","LIAR","LICE","LICK","LIED","LIEN","LIES","LIEU","LIFE","LIFT","LIKE","LILA","LILT","LILY","LIMA","LIMB","LIME","LIND","LINE","LINK","LINT","LION","LISA","LIST","LIVE","LOAD","LOAF","LOAM","LOAN","LOCK","LOFT","LOGE","LOIS","LOLA","LONE","LONG","LOOK","LOON","LOOT","LORD","LORE","LOSE","LOSS","LOST","LOUD","LOVE","LOWE","LUCK","LUCY","LUGE","LUKE","LULU","LUND","LUNG","LURA","LURE","LURK","LUSH","LUST","LYLE","LYNN","LYON","LYRA","MACE","MADE","MAGI","MAID","MAIL","MAIN","MAKE","MALE","MALI","MALL","MALT","MANA","MANN","MANY","MARC","MARE","MARK","MARS","MART","MARY","MASH","MASK","MASS","MAST","MATE","MATH","MAUL","MAYO","MEAD","MEAL","MEAN","MEAT","MEEK","MEET","MELD","MELT","MEMO","MEND","MENU","MERT","MESH","MESS","MICE","MIKE","MILD","MILE","MILK","MILL","MILT","MIMI","MIND","MINE","MINI","MINK","MINT","MIRE","MISS","MIST","MITE","MITT","MOAN","MOAT","MOCK","MODE","MOLD","MOLE","MOLL","MOLT","MONA","MONK","MONT","MOOD","MOON","MOOR","MOOT","MORE","MORN","MORT","MOSS","MOST","MOTH","MOVE","MUCH","MUCK","MUDD","MUFF","MULE","MULL","MURK","MUSH","MUST","MUTE","MUTT","MYRA","MYTH","NAGY","NAIL","NAIR","NAME","NARY","NASH","NAVE","NAVY","NEAL","NEAR","NEAT","NECK","NEED","NEIL","NELL","NEON","NERO","NESS","NEST","NEWS","NEWT","NIBS","NICE","NICK","NILE","NINA","NINE","NOAH","NODE","NOEL","NOLL","NONE","NOOK","NOON","NORM","NOSE","NOTE","NOUN","NOVA","NUDE","NULL","NUMB","OATH","OBEY","OBOE","ODIN","OHIO","OILY","OINT","OKAY","OLAF","OLDY","OLGA","OLIN","OMAN","OMEN","OMIT","ONCE","ONES","ONLY","ONTO","ONUS","ORAL","ORGY","OSLO","OTIS","OTTO","OUCH","OUST","OUTS","OVAL","OVEN","OVER","OWLY","OWNS","QUAD","QUIT","QUOD","RACE","RACK","RACY","RAFT","RAGE","RAID","RAIL","RAIN","RAKE","RANK","RANT","RARE","RASH","RATE","RAVE","RAYS","READ","REAL","REAM","REAR","RECK","REED","REEF","REEK","REEL","REID","REIN","RENA","REND","RENT","REST","RICE","RICH","RICK","RIDE","RIFT","RILL","RIME","RING","RINK","RISE","RISK","RITE","ROAD","ROAM","ROAR","ROBE","ROCK","RODE","ROIL","ROLL","ROME","ROOD","ROOF","ROOK","ROOM","ROOT","ROSA","ROSE","ROSS","ROSY","ROTH","ROUT","ROVE","ROWE","ROWS","RUBE","RUBY","RUDE","RUDY","RUIN","RULE","RUNG","RUNS","RUNT","RUSE","RUSH","RUSK","RUSS","RUST","RUTH","SACK","SAFE","SAGE","SAID","SAIL","SALE","SALK","SALT","SAME","SAND","SANE","SANG","SANK","SARA","SAUL","SAVE","SAYS","SCAN","SCAR","SCAT","SCOT","SEAL","SEAM","SEAR","SEAT","SEED","SEEK","SEEM","SEEN","SEES","SELF","SELL","SEND","SENT","SETS","SEWN","SHAG","SHAM","SHAW","SHAY","SHED","SHIM","SHIN","SHOD","SHOE","SHOT","SHOW","SHUN","SHUT","SICK","SIDE","SIFT","SIGH","SIGN","SILK","SILL","SILO","SILT","SINE","SING","SINK","SIRE","SITE","SITS","SITU","SKAT","SKEW","SKID","SKIM","SKIN","SKIT","SLAB","SLAM","SLAT","SLAY","SLED","SLEW","SLID","SLIM","SLIT","SLOB","SLOG","SLOT","SLOW","SLUG","SLUM","SLUR","SMOG","SMUG","SNAG","SNOB","SNOW","SNUB","SNUG","SOAK","SOAR","SOCK","SODA","SOFA","SOFT","SOIL","SOLD","SOME","SONG","SOON","SOOT","SORE","SORT","SOUL","SOUR","SOWN","STAB","STAG","STAN","STAR","STAY","STEM","STEW","STIR","STOW","STUB","STUN","SUCH","SUDS","SUIT","SULK","SUMS","SUNG","SUNK","SURE","SURF","SWAB","SWAG","SWAM","SWAN","SWAT","SWAY","SWIM","SWUM","TACK","TACT","TAIL","TAKE","TALE","TALK","TALL","TANK","TASK","TATE","TAUT","TEAL","TEAM","TEAR","TECH","TEEM","TEEN","TEET","TELL","TEND","TENT","TERM","TERN","TESS","TEST","THAN","THAT","THEE","THEM","THEN","THEY","THIN","THIS","THUD","THUG","TICK","TIDE","TIDY","TIED","TIER","TILE","TILL","TILT","TIME","TINA","TINE","TINT","TINY","TIRE","TOAD","TOGO","TOIL","TOLD","TOLL","TONE","TONG","TONY","TOOK","TOOL","TOOT","TORE","TORN","TOTE","TOUR","TOUT","TOWN","TRAG","TRAM","TRAY","TREE","TREK","TRIG","TRIM","TRIO","TROD","TROT","TROY","TRUE","TUBA","TUBE","TUCK","TUFT","TUNA","TUNE","TUNG","TURF","TURN","TUSK","TWIG","TWIN","TWIT","ULAN","UNIT","URGE","USED","USER","USES","UTAH","VAIL","VAIN","VALE","VARY","VASE","VAST","VEAL","VEDA","VEIL","VEIN","VEND","VENT","VERB","VERY","VETO","VICE","VIEW","VINE","VISE","VOID","VOLT","VOTE","WACK","WADE","WAGE","WAIL","WAIT","WAKE","WALE","WALK","WALL","WALT","WAND","WANE","WANG","WANT","WARD","WARM","WARN","WART","WASH","WAST","WATS","WATT","WAVE","WAVY","WAYS","WEAK","WEAL","WEAN","WEAR","WEED","WEEK","WEIR","WELD","WELL","WELT","WENT","WERE","WERT","WEST","WHAM","WHAT","WHEE","WHEN","WHET","WHOA","WHOM","WICK","WIFE","WILD","WILL","WIND","WINE","WING","WINK","WINO","WIRE","WISE","WISH","WITH","WOLF","WONT","WOOD","WOOL","WORD","WORE","WORK","WORM","WORN","WOVE","WRIT","WYNN","YALE","YANG","YANK","YARD","YARN","YAWL","YAWN","YEAH","YEAR","YELL","YOGA","YOKE"]');

/***/ })

/******/ 	});
/************************************************************************/
/******/ 	// The module cache
/******/ 	var __webpack_module_cache__ = {};
/******/ 	
/******/ 	// The require function
/******/ 	function __webpack_require__(moduleId) {
/******/ 		// Check if module is in cache
/******/ 		var cachedModule = __webpack_module_cache__[moduleId];
/******/ 		if (cachedModule !== undefined) {
/******/ 			return cachedModule.exports;
/******/ 		}
/******/ 		// Create a new module (and put it into the cache)
/******/ 		var module = __webpack_module_cache__[moduleId] = {
/******/ 			// no module.id needed
/******/ 			// no module.loaded needed
/******/ 			exports: {}
/******/ 		};
/******/ 	
/******/ 		// Execute the module function
/******/ 		__webpack_modules__[moduleId].call(module.exports, module, module.exports, __webpack_require__);
/******/ 	
/******/ 		// Return the exports of the module
/******/ 		return module.exports;
/******/ 	}
/******/ 	
/************************************************************************/
/******/ 	
/******/ 	// startup
/******/ 	// Load entry module and return exports
/******/ 	// This entry module is referenced by other modules so it can't be inlined
/******/ 	var __webpack_exports__ = __webpack_require__("./dist/npm/index.js");
/******/ 	xrpl = __webpack_exports__;
/******/ 	
/******/ })()
;
//# sourceMappingURL=xrpl-latest.js.map