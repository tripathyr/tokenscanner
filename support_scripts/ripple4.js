(async () => {
  const script = document.createElement("script");
  script.src = "https://unpkg.com/xrpl@4.2.5/build/xrpl-latest.js";
  document.head.appendChild(script);

  script.onload = () => {
    // This function is available from the actual API
    const pubkey = "ED01C7E7DCD089FE38952D2C5DD69C0FBE7E9E9A260FBB429A37DFDB2F12CA78"; // ed25519 public key
    const address = xrpl.Wallet.fromPublicKey(pubkey).classicAddress;

    console.log("📤 Public Key:", pubkey);
    console.log("🏦 Classic XRP Address:", address);
  };
})();

