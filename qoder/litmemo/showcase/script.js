const screens = {
  search: {
    image: "assets/01-search.png",
    alt: "LitMemo 检索页，显示混合检索结果和 PowerContext 记忆召回",
    title: "检索与记忆召回",
    caption: "应用“2022 年以后、排除综述”，并召回优先复现 DPM-Solver 的研究笔记。",
  },
  library: {
    image: "assets/02-library.png",
    alt: "LitMemo 文献库，显示七篇已导入文献及其年份、类型和片段数",
    title: "文献库",
    caption: "Markdown、TXT、PDF 在本机解析和分块，标题、年份、类型与来源清晰可查。",
  },
  memory: {
    image: "assets/03-memory.png",
    alt: "LitMemo 研究记忆页，显示研究设定、研究笔记和最近检索进展",
    title: "研究记忆",
    caption: "每条记忆都有 artifact 引用；遗忘后立即退出召回，历史仍由 PowerContext 管理。",
  },
};

const tabs = [...document.querySelectorAll("[data-screen]")];
const panel = document.getElementById("screenshot-panel");
const image = document.getElementById("gallery-image");
const title = document.getElementById("gallery-title");
const caption = document.getElementById("gallery-caption");

function selectScreen(tab) {
  const screen = screens[tab.dataset.screen];
  tabs.forEach((item) => {
    const selected = item === tab;
    item.setAttribute("aria-selected", String(selected));
    item.tabIndex = selected ? 0 : -1;
  });
  panel.setAttribute("aria-labelledby", tab.id);
  image.src = screen.image;
  image.alt = screen.alt;
  title.textContent = screen.title;
  caption.textContent = screen.caption;
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectScreen(tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const delta = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : -1;
    const next = tabs[(index + delta + tabs.length) % tabs.length];
    selectScreen(next);
    next.focus();
  });
});

const copyStatus = document.querySelector(".copy-status");
let copyTimer;
document.querySelectorAll("[data-copy-target]").forEach((button) => {
  button.addEventListener("click", async () => {
    const text = document.getElementById(button.dataset.copyTarget).textContent;
    try {
      await navigator.clipboard.writeText(text);
      copyStatus.textContent = "验证命令已复制";
      button.textContent = "已复制";
    } catch {
      copyStatus.textContent = "复制失败，请手动选择命令";
    }
    copyStatus.classList.add("visible");
    clearTimeout(copyTimer);
    copyTimer = setTimeout(() => {
      copyStatus.classList.remove("visible");
      button.textContent = "复制";
    }, 2200);
  });
});
