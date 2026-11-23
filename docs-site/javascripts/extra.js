/* Mermaid, if a page still has a pre.mermaid fence, is rendered in Night Watch colors. */
(function () {
  function vars(dark) {
    if (dark) {
      return {
        darkMode: true,
        background: "#0B1220",
        primaryColor: "#141C2B",
        primaryTextColor: "#E8EEF6",
        primaryBorderColor: "#2A3548",
        secondaryColor: "#1C2636",
        tertiaryColor: "#0B1220",
        lineColor: "#7EB8C9",
        textColor: "#E8EEF6",
        mainBkg: "#141C2B",
        nodeBorder: "#2A3548",
        clusterBkg: "#141C2B",
        clusterBorder: "#2A3548",
        titleColor: "#E8EEF6",
        edgeLabelBackground: "#0B1220",
        nodeTextColor: "#E8EEF6",
      };
    }
    return {
      darkMode: false,
      background: "#F4F6FA",
      primaryColor: "#E6EAF2",
      primaryTextColor: "#121826",
      primaryBorderColor: "#C5CDD8",
      secondaryColor: "#FFFFFF",
      tertiaryColor: "#F4F6FA",
      lineColor: "#1F6F82",
      textColor: "#121826",
      mainBkg: "#E6EAF2",
      nodeBorder: "#C5CDD8",
      clusterBkg: "#E6EAF2",
      clusterBorder: "#C5CDD8",
      titleColor: "#121826",
      edgeLabelBackground: "#F4F6FA",
      nodeTextColor: "#121826",
    };
  }

  async function renderAll() {
    if (!window.mermaid) return;
    var dark = (document.body.getAttribute("data-md-color-scheme") || "slate") === "slate";
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: "loose",
      theme: "base",
      themeVariables: vars(dark),
    });
    var nodes = document.querySelectorAll("pre.mermaid");
    for (var i = 0; i < nodes.length; i++) {
      var pre = nodes[i];
      var src = (pre.textContent || "").trim();
      if (!src) continue;
      var id = "nw-mermaid-" + i + "-" + Date.now();
      try {
        var out = await window.mermaid.render(id, src);
        var wrap = document.createElement("div");
        wrap.className = "mermaid";
        wrap.innerHTML = out.svg;
        pre.replaceWith(wrap);
      } catch (e) {
        console.warn("mermaid render failed", e);
      }
    }
  }

  if (typeof document$ !== "undefined") {
    document$.subscribe(function () { renderAll(); });
  } else {
    document.addEventListener("DOMContentLoaded", renderAll);
  }
})();
