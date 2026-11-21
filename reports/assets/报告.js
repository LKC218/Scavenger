"use strict";

// 图示只表达空间关系，不对应原视频的数量、地图和实际规则。
const svgNS = "http://www.w3.org/2000/svg";
const swarm = document.getElementById("swarm");
let seed = 17;
const random = () => {
  seed = (seed * 16807) % 2147483647;
  return (seed - 1) / 2147483646;
};
for (let i = 0; i < 92; i += 1) {
  const dot = document.createElementNS(svgNS, "circle");
  const depth = random();
  dot.setAttribute("cx", String(340 + (random() - 0.5) * (85 + depth * 290)));
  dot.setAttribute("cy", String(192 + depth * 144));
  dot.setAttribute("r", String(1.3 + random() * 1.7));
  swarm.append(dot);
}

// 导航只反映当前阅读位置。
const navLinks = Array.from(document.querySelectorAll(".sidebar nav a"));
if ("IntersectionObserver" in window) {
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      for (const link of navLinks) {
        const active = link.hash === `#${entry.target.id}`;
        link.classList.toggle("active", active);
        if (active) link.setAttribute("aria-current", "location");
        else link.removeAttribute("aria-current");
      }
    }
  }, { rootMargin: "-8% 0px -65% 0px", threshold: 0 });
  document.querySelectorAll("main .section[id]").forEach(section => observer.observe(section));
}

document.querySelectorAll(".print-button").forEach(button => {
  button.addEventListener("click", () => window.print());
});

const filterButtons = Array.from(document.querySelectorAll("[data-filter]"));
const observations = Array.from(document.querySelectorAll("[data-kind]"));
for (const button of filterButtons) {
  button.addEventListener("click", () => {
    for (const filter of filterButtons) {
      const active = filter === button;
      filter.classList.toggle("selected", active);
      filter.setAttribute("aria-pressed", String(active));
    }
    let count = 0;
    for (const observation of observations) {
      const visible = button.dataset.filter === "all" || observation.dataset.kind === button.dataset.filter;
      observation.hidden = !visible;
      if (visible) count += 1;
    }
    document.getElementById("evidence-count").textContent = `${count} 条记录`;
  });
}

const layers = [
  ["全局状态", "观察：顶部存在多个数值和阶段字样。", "将全局状态固定在稳定区域，只展示会影响当前决策的指标；不要让普通数值和紧急告警争夺同一层级。", "验证：玩家能否迅速指出当前阶段和最紧迫问题？"],
  ["主战场", "观察：斜俯视战场中同时出现防线、敌群和战斗效果。", "对象轮廓、地形和特效应保持清晰层级。判断可操作对象时，同时提供形状和文字提示，避免仅靠颜色。", "验证：当火光与烟尘增多时，能否辨认当前对象和目标？"],
  ["事件提示", "观察：主视图左侧集中出现多条战场事件。", "优先显示需要立即处理的异常；合并同类提示，避免刷屏。点击提示定位镜头可以作为后续假设，原作是否支持尚未核实。", "验证：新提示是否打断当前操作，玩家能否找到事件发生处？"],
  ["小地图", "观察：底部左侧存在战场缩略总览。", "用简化形状表达空间关系与关注区域。小地图导航和主视图联动单独试验，原片镜头变化未能确定其输入方式。", "验证：玩家能否将小地图上的位置与主战场对应起来？"],
  ["当前对象", "观察：单选显示单位属性，多选显示卡片与汇总；双小队矩形选择已确认。", "将信息栏绑定到明确的选择状态。没有选择时显示操作提示，选择改变后同步对象名称、可用动作和场景轮廓。", "验证：玩家是否始终知道当前命令会发送给谁？"]
];
const hotspots = Array.from(document.querySelectorAll("[data-layer]"));
for (const button of hotspots) {
  button.addEventListener("click", () => {
    const index = Number(button.dataset.layer);
    const layer = layers[index];
    for (const hotspot of hotspots) {
      hotspot.classList.toggle("current", hotspot === button);
      hotspot.setAttribute("aria-pressed", String(hotspot === button));
    }
    document.getElementById("layer-number").textContent = `信息层 / ${String(index + 1).padStart(2, "0")}`;
    document.getElementById("layer-title").textContent = layer[0];
    document.getElementById("layer-observed").textContent = layer[1];
    document.getElementById("layer-proposal").textContent = layer[2];
    document.getElementById("layer-test").textContent = layer[3];
  });
}

const steps = [
  ["观察阶段", "先发现战线与局部异常。用轮廓、留白和提示优先级区分可控制对象、敌群与环境，避免把判断负担交给特效。", "典型风险：烟尘遮住对象，玩家失去判断依据。"],
  ["选择阶段", "选择完成后，在场景、信息栏和可用动作中同步显示当前对象。点击空白处和取消选择时，应回到可解释的默认状态。", "典型风险：场景轮廓与面板展示了不同对象。"],
  ["下令阶段", "把用户意图转为明确动作，并校验目标是否合法。移动、攻击或其他动作的优先关系要有一致规则；具体按键仍需另行设计。", "典型风险：点击界面穿透到战场，意外产生移动命令。"],
  ["反馈阶段", "命令被接受后立即显示目标与状态。完成、取消和无效命令使用不同文字或形状反馈，减少玩家反复点击确认的负担。", "典型风险：没有任何反馈，玩家以为点击未生效。"],
  ["调整阶段", "允许重新选择和改变目标。切换页面、失焦或暂停时处理未完成动作；恢复操作应可预测，避免旧指令突然继续。", "典型风险：返回页面后对象瞬移，或持续输入没有清除。"]
];
const stepButtons = Array.from(document.querySelectorAll("[data-step]"));
for (const button of stepButtons) {
  button.addEventListener("click", () => {
    for (const step of stepButtons) {
      step.classList.toggle("current", step === button);
      step.setAttribute("aria-pressed", String(step === button));
    }
    const step = steps[Number(button.dataset.step)];
    document.getElementById("step-label").textContent = step[0];
    document.getElementById("step-description").textContent = step[1];
    document.getElementById("step-risk").textContent = step[2];
  });
}

// 演示使用时间戳差值插值；页面隐藏时停止，不累计后台时间。
const lab = document.querySelector(".lab");
const labState = document.getElementById("lab-state");
const commandPath = document.getElementById("command-path");
const commandMarker = document.getElementById("command-marker");
const unitButtons = Array.from(document.querySelectorAll("[data-unit]"));
const targetButtons = Array.from(document.querySelectorAll("[data-target]"));
const units = { a: { x: 190, y: 205 }, b: { x: 290, y: 205 } };
const targets = { north: { x: 240, y: 60, name: "北侧" }, west: { x: 90, y: 135, name: "西侧" }, east: { x: 390, y: 135, name: "东侧" } };
let selectedUnit = null;
let animationFrame = null;
const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

function stopMovement() {
  if (animationFrame !== null) cancelAnimationFrame(animationFrame);
  animationFrame = null;
  commandPath.setAttribute("d", "");
  commandMarker.setAttribute("hidden", "");
}

function updateUnitPosition(id) {
  document.getElementById(`unit-${id}`).setAttribute("transform", `translate(${units[id].x} ${units[id].y})`);
}

function selectUnit(id) {
  stopMovement();
  selectedUnit = id;
  for (const unit of Object.keys(units)) {
    document.getElementById(`unit-${unit}`).classList.toggle("selected", unit === id);
  }
  for (const button of unitButtons) {
    const active = button.dataset.unit === id;
    button.classList.toggle("is-selected", active);
    button.setAttribute("aria-pressed", String(active));
  }
  for (const button of targetButtons) button.disabled = !id;
  labState.textContent = id ? `已选中 ${id.toUpperCase()} 小队：请选择移动目标` : "待选择：请先选择一个小队";
}

for (const button of unitButtons) {
  button.setAttribute("aria-pressed", "false");
  button.addEventListener("click", () => selectUnit(button.dataset.unit));
}
document.getElementById("cancel-selection").addEventListener("click", () => selectUnit(null));
document.addEventListener("keydown", event => {
  if (event.key === "Escape" && lab.contains(document.activeElement)) selectUnit(null);
});
for (const button of targetButtons) {
  button.addEventListener("click", () => {
    if (!selectedUnit) return;
    stopMovement();
    const id = selectedUnit;
    const destination = targets[button.dataset.target];
    const start = { ...units[id] };
    commandPath.setAttribute("d", `M${start.x} ${start.y}L${destination.x} ${destination.y}`);
    commandMarker.setAttribute("cx", String(destination.x));
    commandMarker.setAttribute("cy", String(destination.y));
    commandMarker.removeAttribute("hidden");
    labState.textContent = `${id.toUpperCase()} 小队：命令已接收，前往${destination.name}`;
    const startedAt = performance.now();
    function move(timestamp) {
      const progress = reducedMotion.matches ? 1 : Math.min(1, Math.max(0, (timestamp - startedAt) / 1000));
      const eased = 1 - (1 - progress) ** 3;
      units[id].x = start.x + (destination.x - start.x) * eased;
      units[id].y = start.y + (destination.y - start.y) * eased;
      updateUnitPosition(id);
      if (progress < 1) animationFrame = requestAnimationFrame(move);
      else {
        animationFrame = null;
        labState.textContent = `${id.toUpperCase()} 小队：已到达${destination.name}，可继续下令或取消`;
      }
    }
    animationFrame = requestAnimationFrame(move);
  });
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden && animationFrame !== null) {
    stopMovement();
    labState.textContent = "移动已停止：返回后请重新下令";
  }
});

const directions = {
  defense: ["俯视观察、单位选择、位置命令、战线告警与局部反馈。", "敌群压力曲线、通路拥堵、目标优先级与失败后的恢复体验。", "用占位对象调动两支队伍，检查玩家能否识别压力点并及时调整位置。"],
  recovery: ["对象选择、地图总览、位置命令和事件提示；“观察—下令—反馈”仍可复用。", "路线选择、携带成本、风险收益、撤回条件与局内外循环需要单独设计。", "在小区域中布置两个回收点，让玩家安排往返顺序，检查目标与收益是否清楚。"],
  ecology: ["队伍调度、区域状态、优先级提示和目标反馈，不依赖僵尸题材。", "修复规则、区域连锁影响、非战斗反馈，以及玩家如何理解变化原因。", "用两个队伍处理三处异常，检查玩家能否从提示中判断处理顺序。"]
};
const directionButtons = Array.from(document.querySelectorAll("[data-direction]"));
for (const button of directionButtons) {
  button.addEventListener("click", () => {
    for (const candidate of directionButtons) {
      candidate.classList.toggle("current", candidate === button);
      candidate.setAttribute("aria-pressed", String(candidate === button));
    }
    const direction = directions[button.dataset.direction];
    ["direction-reuse", "direction-risk", "direction-test"].forEach((id, index) => {
      document.getElementById(id).textContent = direction[index];
    });
  });
}

// 只保存非敏感的本机阅读标记；存储不可用时仍允许临时勾选。
const storageKey = "restAND-report-reading-v1";
const checkboxes = Array.from(document.querySelectorAll("[data-check]"));
let storedChecks = {};
try {
  const parsed = JSON.parse(localStorage.getItem(storageKey) || "{}");
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) storedChecks = parsed;
} catch {
  document.getElementById("storage-note").textContent = "本机存储不可用，本次标记只在当前页面保留。";
}
for (const input of checkboxes) {
  input.checked = storedChecks[input.dataset.check] === true;
  input.addEventListener("change", () => {
    storedChecks[input.dataset.check] = input.checked;
    try {
      localStorage.setItem(storageKey, JSON.stringify(storedChecks));
    } catch {
      document.getElementById("storage-note").textContent = "本机存储不可用，本次标记只在当前页面保留。";
    }
  });
}
document.getElementById("reset-checklist").addEventListener("click", () => {
  storedChecks = {};
  for (const input of checkboxes) input.checked = false;
  try { localStorage.removeItem(storageKey); } catch { /* 清除页面内状态仍然生效。 */ }
});
