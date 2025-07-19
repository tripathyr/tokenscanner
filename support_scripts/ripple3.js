(async () => {
  const script = document.createElement("script");
  script.src = "https://unpkg.com/xrpl@4.2.5/build/xrpl-latest.js";
  document.head.appendChild(script);

  script.onload = () => {
    const wallet = xrpl.Wallet.generate({ algorithm: "secp256k1" });

    console.log("🧪 ALGORITHM: secp256k1 (Bitcoin-style)");
    console.log("🔐 Seed:", wallet.seed);           // starts with 's...'
    console.log("📤 Public Key:", wallet.publicKey); // 33 bytes, starts with 02/03
    console.log("🏦 XRP Address:", wallet.classicAddress); // starts with r...
  };
})();

