(() => {
  try {
    const savedTheme = localStorage.getItem("gatewatch-theme");
    document.documentElement.dataset.theme = savedTheme === "dark" ? "dark" : "light";
  } catch {
    document.documentElement.dataset.theme = "light";
  }
})();
