const script = document.createElement("script");
script.src = "https://unpkg.com/xrpl@4.2.5/build/xrpl-latest.js";
document.head.appendChild(script);

script.onload = () => {
  const wallet = xrpl.Wallet.generate();
  console.log("Seed:", wallet.seed);
  console.log("Public Key:", wallet.publicKey);
  console.log("Address:", wallet.classicAddress);
};
