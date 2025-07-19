(async () => {
  // Load xrpl.js dynamically
  const script = document.createElement("script");
  script.src = "https://unpkg.com/xrpl@4.2.5/build/xrpl-latest.js";
  document.head.appendChild(script);

  // Wait for the script to load
  script.onload = () => {
    try {
      // ✅ Use a known valid ed25519 testnet seed
      const seed = "sEd7X9nL7hWw5dTCk1oMzk9H6qWYXEJ";

      const wallet = xrpl.Wallet.fromSeed(seed);

      console.log("✅ Seed:", wallet.seed);
      console.log("📤 Public Key:", wallet.publicKey);
      console.log("🏦 XRP Address:", wallet.classicAddress);
    } catch (e) {
      console.error("❌ Error:", e.message);
    }
  };
})();
