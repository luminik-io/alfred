(() => {
  const root = document.documentElement;
  const themes = new Set([
    "signal-edge",
    "category-standard",
    "linked-fold",
  ]);
  let theme = "signal-edge";
  let mode = "light";
  try {
    const savedTheme = localStorage.getItem("alfred-theme-name");
    const savedMode = localStorage.getItem("alfred-theme");
    if (themes.has(savedTheme)) theme = savedTheme;
    if (savedMode === "dark" || savedMode === "light") mode = savedMode;
  } catch {
    // Storage can be disabled. Prism light remains the safe default.
  }
  root.dataset.theme = theme;
  root.classList.add(mode === "dark" ? "dark" : "light");
})();
