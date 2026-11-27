(function () {
  "use strict";

  var displayedSnapshot = null;
  var displayedSnapshotId = null;
  var stagingSnapshot = null;
  var queuedSnapshot = null;
  var latestStatusEnvelope = null;
  var latestSeenStateSeq = -1;
  var liveState = "connecting";
  var activeFrameSlot = "a";
  var transitionToken = 0;
  var pendingPollTimer = null;
  var retryFetchTimer = null;
  // Scene frames abandoned in a row without a commit (refused, failed to start or failed to load). Each fresh retry
  // waits longer, up to RETRY_MAX_MS, so a failure that persists never spins; a commit resets the count.
  var abandonedFrames = 0;
  var RETRY_BASE_MS = 200;
  var RETRY_MAX_MS = 5000;
  // The viewer process the page follows (server.instance_id of /api/snapshot) and the ones it has left behind.
  var serverInstanceId = null;
  var retiredServerInstances = {};
  // /api/snapshot reads: the last one issued, the newest one answered, failures in a row and the pending retry.
  var snapshotRequestSeq = 0;
  var snapshotAnsweredSeq = 0;
  var snapshotFailures = 0;
  var snapshotRetryTimer = null;
  var eventsOpen = false;

  var CHANNEL_COLORS = {
    DETECTED: "#0d8a5f",
    ABSORBED: "#2563eb",
    OUTSIDE_ACTIVE_AREA: "#d97706",
    ESCAPED: "#64748b",
    PATH_LIMIT: "#dc2626",
    STEP_LIMIT: "#b91c1c",
    INTERACTION_LIMIT: "#ea580c",
    TRUNCATED: "#e11d48",
    LOSS: "#7c3aed",
    SCATTERED: "#0891b2"
  };
  var FALLBACK_COLORS = ["#0891b2", "#7c3aed", "#db2777", "#65a30d", "#4f46e5", "#0284c7"];

  var STRINGS = {
    en: {
      appTitle: "OS Optics Local Viewer",
      readOnly: "Read-only",
      liveConnected: "Live",
      liveConnecting: "Connecting",
      liveReconnecting: "Reconnecting",
      livePolling: "Polling",
      liveDisconnected: "Disconnected",
      modeComputed: "Computed Result",
      modePreview: "Geometry Preview · Not Computed",
      modeStale: "Stale Run · Project Updated",
      modeNone: "Waiting for Valid Input",
      geomOnlyBadge: "Geometry Only",
      schematicBadge: "Schematic Parts",
      sceneTitle: "Optical Path & Traced Rays",
      updating3d: "Syncing 3D scene…",
      updatingRev: "Syncing Rev #{rev}…",
      execTitle: "Result Status",
      powerTitle: "Power Ledger",
      targetsTitle: "Target Assessments",
      quantitiesTitle: "Observation Planes",
      branchesTitle: "Traced Branches",
      detailsTitle: "Snapshot & Source Details",
      partsUnit: "parts",
      branchesUnit: "branches",
      drawableRaysUnit: "drawable rays",
      crossingsUnit: "plane crossings",
      summaryRaysTag: "summary ray data",
      noRaysPreview: "Geometry preview — no traced rays",
      kpiExecution: "Execution",
      kpiRequirements: "Requirements",
      kpiIntegrity: "Integrity & Freshness",
      notComputed: "Not computed",
      completeLabel: "Complete",
      partialLabel: "Partial",
      partialSub: "Budget reached",
      failedLabel: "Failed",
      targetsCountSub: "{n} targets",
      inScope: "In model scope",
      outOfScope: "Out of model scope",
      consistent: "Consistent",
      integrityFailed: "Hash mismatch",
      notChecked: "Not checked",
      freshCurrent: "Current",
      freshHistorical: "Historical",
      freshUndetermined: "Undetermined",
      freshRunOnly: "Run only",
      freshNoRun: "No saved run",
      conservedYes: "Power conserved",
      conservedNo: "Residual outside tolerance",
      totalInput: "Total Input Power",
      residualPower: "Power Residual",
      colChannel: "Channel",
      colPower: "Power",
      colShare: "Share",
      colElement: "Element",
      colTarget: "Target",
      colAssessment: "Assessment",
      colValue: "Measured / Bounds",
      colPlane: "Plane",
      colState: "State",
      colTotalPower: "Total Power",
      colCompleteness: "Completeness",
      colBranch: "Branch",
      colOrigin: "Origin",
      colWavelength: "Wavelength",
      colFinalPower: "Final Power",
      colTermination: "Termination",
      byElementSummary: "Power breakdown by element",
      noTargets: "No targets declared in this Project.",
      noQuantities: "No observation planes declared.",
      noBranchesComputed: "No traced branches recorded.",
      noBranchesPreview: "No ray tracing has been run for this view.",
      powerUnavailableNoRun: "No saved run is attached to this Project. Showing nominal geometry only — this is not a computed optical result.",
      powerUnavailableStale: "The attached run belongs to an earlier Project revision. Old power figures and rays are withheld so outdated evidence is never mixed with the updated geometry.",
      powerUnavailableConfigNotInRun: "The selected configuration is declared in the Project but was not recorded in the attached run. Showing this configuration's geometry preview only.",
      powerUnavailableOther: "Power ledger is unavailable for this snapshot.",
      tryEvaluateHint: "To run a one-shot local calculation without editing your Project file:",
      waitingTag: "Waiting",
      errorTag: "Error",
      waitingHolding: "Input is incomplete or failed verification — holding last verified snapshot (Rev #{rev}) so geometry and numbers stay consistent. Updates automatically once valid files settle.",
      waitingIntegrityHolding: "Run files failed manifest integrity check (incomplete or mixed write) — holding last verified snapshot (Rev #{rev}). Updates automatically once valid files settle.",
      exhaustedBudgets: "Compute budget reached: {list}. Bounded or indeterminate values reflect untraced power.",
      validationProblems: "Project validation reported {count} issue(s). Expand Snapshot & Source Details to inspect.",
      historicalTitle: "Withheld Historical Run Reference",
      detailInputPath: "Input path",
      detailProjectPath: "Project file",
      detailRunDir: "Run directory",
      detailRootRunDir: "Root run directory",
      detailConfig: "Configuration",
      detailSnapshotId: "Snapshot ID",
      detailProjectHash: "Project hash",
      detailRootProjectHash: "Root project hash",
      detailInputHash: "Engine input hash",
      detailResultHash: "Result hash",
      detailSceneHash: "Scene hash",
      detailRayDataMode: "Ray data",
      detailGeometrySources: "Geometry sources",
      detailSchematicParts: "Schematic parts",
      detailPlaceholderParts: "View placeholders",
      detailVersions: "Versions",
      detailEvalMode: "Evaluation mode",
      evalModeNone: "Direct read-only input",
      evalModeTemp: "Explicit one-shot --evaluate (temp dir, input untouched)",
      completeTag: "Complete",
      boundedTag: "Bounded",
      incompleteTag: "Incomplete",
      uncomputedTag: "Uncomputed",
      upperBoundFmt: "≤ {val}",
      untracedFmt: "untraced {val}",
      primaryOrigin: "Primary beam",
      origins: {
        beam_splitter_reflection: "Beamsplitter reflection",
        beam_splitter_transmission: "Beamsplitter transmission",
        reflection: "Reflection",
        transmission: "Transmission",
        diffraction: "Diffraction"
      },
      noticeTagInfo: "Info",
      noticeTagWarn: "Notice",
      noticeTagErr: "Error",
      channels: {
        DETECTED: "Detected",
        ABSORBED: "Absorbed",
        OUTSIDE_ACTIVE_AREA: "Outside active area",
        ESCAPED: "Escaped",
        PATH_LIMIT: "Path budget limit",
        STEP_LIMIT: "Step budget limit",
        INTERACTION_LIMIT: "Interaction limit",
        TRUNCATED: "Truncated"
      },
      assessments: {
        MET: "Met",
        UNMET: "Unmet",
        INDETERMINATE: "Indeterminate",
        NOT_APPLICABLE: "N/A",
        NOT_COMPUTED: "Not computed",
        STALE_RUN: "Stale run"
      },
      reqOutcomes: {
        PASS: "All met",
        FAIL: "Unmet target",
        UNKNOWN: "Indeterminate",
        INDETERMINATE: "Indeterminate",
        NO_TARGETS: "No targets",
        NOT_APPLICABLE: "Not applicable",
        NOT_COMPUTED: "Not computed",
        STALE_RUN: "Stale run"
      },
      qtyStates: {
        KNOWN: "Known",
        KNOWN_ZERO: "Known (0)",
        BOUNDED: "Bounded",
        UNKNOWN: "Unknown",
        UNCOMPUTED: "Uncomputed",
        NOT_APPLICABLE: "N/A"
      },
      notices: {
        GEOMETRY_PREVIEW_ONLY: "Geometry preview only — no saved run is attached, so optical results and power values are not computed yet.",
        STALE_RUN_MISMATCH: "The attached run is outdated relative to the current Project ({reasons}). Showing current geometry only; old numbers and rays are withheld.",
        EXECUTION_PARTIAL: "Simulation reached a compute budget ({reasons}). Some quantities or target assessments are bounded or indeterminate.",
        RUN_INTEGRITY_FAILED: "Saved run files do not match their manifest hashes — treat this run as compromised or incomplete.",
        OUT_OF_MODEL_SCOPE: "One or more elements or targets fall outside the engine's declared optical model scope.",
        EXPLICIT_LOCAL_EVALUATION: "Evaluated once into a temporary directory via --evaluate. Your input Project file was not modified.",
        EVALUATE_NOT_AUTORERUN: "The Project file changed after the initial --evaluate run. The viewer does not auto-rerun simulations on file edits.",
        RAY_DATA_SUMMARY_ONLY: "Summary ray data (--ray-data summary): drawable branches and power ledger are shown; the optional per-ray plane-crossing table is absent.",
        SCHEMATIC_GEOMETRY_USED: "Schematic placeholder geometry is used for parts without resolved catalog or nominal dimensions ({parts}).",
        CONFIGURATION_NOT_IN_RUN: "Selected configuration is not recorded in the attached run; showing its uncomputed Project geometry preview only."
      }
    },
    zh: {
      appTitle: "OS Optics 本地查看器",
      readOnly: "只读",
      liveConnected: "实时跟随",
      liveConnecting: "连接中",
      liveReconnecting: "重连中",
      livePolling: "轮询中",
      liveDisconnected: "已断开",
      modeComputed: "已计算结果",
      modePreview: "几何预览 · 未计算",
      modeStale: "结果已过期 · 工程已更新",
      modeNone: "等待有效输入",
      geomOnlyBadge: "仅几何预览",
      schematicBadge: "含示意几何",
      sceneTitle: "光路与追迹光线",
      updating3d: "正在同步 3D 光路…",
      updatingRev: "正在同步 Rev #{rev}…",
      execTitle: "结果与执行状态",
      powerTitle: "功率账本",
      targetsTitle: "指标达成评估",
      quantitiesTitle: "观测面物理量",
      branchesTitle: "光线分支明细",
      detailsTitle: "快照绑定与来源详情",
      partsUnit: "个元件",
      branchesUnit: "条分支",
      drawableRaysUnit: "条可绘光线",
      crossingsUnit: "条平面交叉",
      summaryRaysTag: "摘要光线模式",
      noRaysPreview: "仅几何布局预览，尚无追迹光线",
      kpiExecution: "执行状态",
      kpiRequirements: "指标要求",
      kpiIntegrity: "完整性与新鲜度",
      notComputed: "尚未计算",
      completeLabel: "已完成",
      partialLabel: "部分完成",
      partialSub: "触及计算预算",
      failedLabel: "执行失败",
      targetsCountSub: "共 {n} 项指标",
      inScope: "模型范围内",
      outOfScope: "超出模型范围",
      consistent: "校验一致",
      integrityFailed: "哈希不一致",
      notChecked: "未校验",
      freshCurrent: "最新匹配",
      freshHistorical: "历史过期",
      freshUndetermined: "无法判定",
      freshRunOnly: "仅打开运行目录",
      freshNoRun: "无关联运行",
      conservedYes: "功率守恒",
      conservedNo: "残差超容差",
      totalInput: "总输入功率",
      residualPower: "功率残差",
      colChannel: "功率去向通道",
      colPower: "功率",
      colShare: "占比",
      colElement: "元件",
      colTarget: "目标指标",
      colAssessment: "判定",
      colValue: "实测值 / 约束范围",
      colPlane: "观测面",
      colState: "状态",
      colTotalPower: "总功率",
      colCompleteness: "完备性",
      colBranch: "分支",
      colOrigin: "来源",
      colWavelength: "波长",
      colFinalPower: "末端功率",
      colTermination: "终止状态",
      byElementSummary: "按元件查看功率去向",
      noTargets: "当前工程未声明目标指标。",
      noQuantities: "当前工程未声明观测面。",
      noBranchesComputed: "暂无光线分支记录。",
      noBranchesPreview: "当前为几何预览模式，尚未执行光线追迹。",
      powerUnavailableNoRun: "当前工程未关联已保存的计算结果。界面仅展示标称几何布局，不代表任何光学计算结果。",
      powerUnavailableStale: "关联的运行结果对应较早版本的工程。为防止把旧数据错误归因到新设计，旧功率账本与光线已隐藏，仅展示最新工程几何。",
      powerUnavailableConfigNotInRun: "所选构型在工程中已声明，但未被关联的运行目录记录；当前仅展示该构型的标称几何预览（未计算）。",
      powerUnavailableOther: "当前快照无可用功率账本。",
      tryEvaluateHint: "如需在不修改原工程文件的前提下执行一次本地计算：",
      waitingTag: "等待写入",
      errorTag: "读取异常",
      waitingHolding: "检测到文件未写齐或校验未通过——已保持上一份有效快照（Rev #{rev}），确保几何与数值严格一致；待文件写入完整后自动恢复更新。",
      waitingIntegrityHolding: "运行目录未通过 manifest 完整性校验（可能处于半写入或新旧文件混杂状态）——已保持上一份有效快照（Rev #{rev}），待有效结果落盘后自动恢复。",
      exhaustedBudgets: "触发计算预算上限：{list}。部分物理量或目标判定为有界区间或待定状态。",
      validationProblems: "工程校验发现 {count} 项问题，可展开下方「快照绑定与来源详情」查看。",
      historicalTitle: "已隔离的历史运行记录（不代表当前工程）",
      detailInputPath: "输入路径",
      detailProjectPath: "工程文件",
      detailRunDir: "运行目录",
      detailRootRunDir: "根运行目录",
      detailConfig: "构型",
      detailSnapshotId: "快照 ID",
      detailProjectHash: "工程哈希",
      detailRootProjectHash: "根工程哈希",
      detailInputHash: "引擎输入哈希",
      detailResultHash: "结果哈希",
      detailSceneHash: "场景哈希",
      detailRayDataMode: "光线数据模式",
      detailGeometrySources: "几何来源",
      detailSchematicParts: "示意占位元件",
      detailPlaceholderParts: "视图标记件",
      detailVersions: "版本信息",
      detailEvalMode: "计算模式",
      evalModeNone: "直接只读本地输入",
      evalModeTemp: "显式单次 --evaluate（临时目录运行，未修改原文件）",
      completeTag: "完整",
      boundedTag: "有界",
      incompleteTag: "不完整",
      uncomputedTag: "未计算",
      upperBoundFmt: "≤ {val}",
      untracedFmt: "未追迹 {val}",
      primaryOrigin: "主光束",
      origins: {
        beam_splitter_reflection: "分束镜反射",
        beam_splitter_transmission: "分束镜透射",
        reflection: "反射分支",
        transmission: "透射分支",
        diffraction: "衍射分支"
      },
      noticeTagInfo: "提示",
      noticeTagWarn: "注意",
      noticeTagErr: "异常",
      channels: {
        DETECTED: "探测器接收",
        ABSORBED: "元件吸收",
        OUTSIDE_ACTIVE_AREA: "有效区外截断",
        ESCAPED: "逸出系统",
        PATH_LIMIT: "路径预算截断",
        STEP_LIMIT: "步数预算截断",
        INTERACTION_LIMIT: "交互次数上限",
        TRUNCATED: "能量截断"
      },
      assessments: {
        MET: "达成",
        UNMET: "未达成",
        INDETERMINATE: "待定",
        NOT_APPLICABLE: "不适用",
        NOT_COMPUTED: "未计算",
        STALE_RUN: "结果过期"
      },
      reqOutcomes: {
        PASS: "全部达成",
        FAIL: "存在未达成",
        UNKNOWN: "待定",
        INDETERMINATE: "待定",
        NO_TARGETS: "无指标",
        NOT_APPLICABLE: "不适用",
        NOT_COMPUTED: "未计算",
        STALE_RUN: "结果过期"
      },
      qtyStates: {
        KNOWN: "确定",
        KNOWN_ZERO: "确定 (0)",
        BOUNDED: "有界区间",
        UNKNOWN: "未知",
        UNCOMPUTED: "未计算",
        NOT_APPLICABLE: "不适用"
      },
      notices: {
        GEOMETRY_PREVIEW_ONLY: "当前仅展示工程几何预览，未关联运行结果，因此尚未生成光学计算与功率数据。",
        STALE_RUN_MISMATCH: "关联的运行结果相对当前工程已过期（{reasons}）。仅展示新工程几何，旧数值与光线已隐藏。",
        EXECUTION_PARTIAL: "本次仿真触发了计算预算上限（{reasons}），部分观测物理量或目标指标处于有界/待定状态。",
        RUN_INTEGRITY_FAILED: "运行目录中的文件与 manifest 校验哈希不一致，结果可能不完整或被改动。",
        OUT_OF_MODEL_SCOPE: "部分元件或目标超出了当前引擎声明的光学模型适用范围。",
        EXPLICIT_LOCAL_EVALUATION: "已通过显式 --evaluate 在临时目录完成一次本地计算，原始工程文件未被修改。",
        EVALUATE_NOT_AUTORERUN: "工程文件在初始 --evaluate 后已修改；为避免频繁重算，查看器不会在文件保存时自动重新运行仿真。",
        RAY_DATA_SUMMARY_ONLY: "当前运行保存为摘要光线模式（--ray-data summary）：3D 光线分支与功率账本完整可用，可选的逐光线平面交叉明细表未记录。",
        SCHEMATIC_GEOMETRY_USED: "部分未计算元件缺少目录或标称尺寸（{parts}），已按示意占位几何绘制并明确标注。",
        CONFIGURATION_NOT_IN_RUN: "所选构型未包含在关联的运行目录中，当前仅展示该构型的未计算工程几何预览。"
      }
    }
  };

  function detectLang() {
    try {
      var params = new URLSearchParams(window.location.search);
      var q = params.get("lang");
      if (q === "zh" || q === "en") {
        return q;
      }
      var saved = window.sessionStorage.getItem("os_optics_viewer_lang");
      if (saved === "zh" || saved === "en") {
        return saved;
      }
    } catch (_e) {
      // ignore storage errors
    }
    var nav = (navigator.language || "en").toLowerCase();
    return nav.indexOf("zh") === 0 ? "zh" : "en";
  }

  var lang = detectLang();

  function t(key, vars) {
    var dict = STRINGS[lang] || STRINGS.en;
    var val = dict[key];
    if (val === undefined) {
      val = STRINGS.en[key] !== undefined ? STRINGS.en[key] : key;
    }
    if (typeof val === "string" && vars) {
      Object.keys(vars).forEach(function (k) {
        val = val.replace("{" + k + "}", String(vars[k]));
      });
    }
    return val;
  }

  function el(id) {
    return document.getElementById(id);
  }

  function getActiveFrame() {
    return el(activeFrameSlot === "a" ? "engine-frame-a" : "engine-frame-b");
  }

  function getStagingFrame() {
    return el(activeFrameSlot === "a" ? "engine-frame-b" : "engine-frame-a");
  }

  function clear(node) {
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function h(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) {
      node.className = cls;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function shortHash(val) {
    if (!val || typeof val !== "string") {
      return "—";
    }
    return val.length > 12 ? val.slice(0, 12) : val;
  }

  function formatPower(watts) {
    if (watts === null || watts === undefined || !isFinite(watts)) {
      return "—";
    }
    var abs = Math.abs(watts);
    if (abs === 0) {
      return "0 W";
    }
    if (abs >= 0.99995) {
      return Number(watts.toPrecision(5)) + " W";
    }
    if (abs >= 0.99995e-3) {
      return Number((watts * 1e3).toPrecision(5)) + " mW";
    }
    if (abs >= 0.99995e-6) {
      return Number((watts * 1e6).toPrecision(5)) + " µW";
    }
    if (abs >= 0.99995e-9) {
      return Number((watts * 1e9).toPrecision(4)) + " nW";
    }
    return watts.toExponential(3) + " W";
  }

  function formatExactWatts(watts) {
    if (watts === null || watts === undefined || !isFinite(watts)) {
      return "";
    }
    return watts === 0 ? "0 W" : watts.toExponential(6) + " W";
  }

  function formatNumber(val, unit) {
    if (val === null || val === undefined) {
      return "—";
    }
    if (typeof val === "number") {
      if (!isFinite(val)) {
        return String(val);
      }
      var abs = Math.abs(val);
      var s;
      if (abs === 0) {
        s = "0";
      } else if (abs >= 1e-3 && abs < 1e5) {
        s = String(Number(val.toPrecision(5)));
      } else {
        s = val.toExponential(3);
      }
      return unit ? s + " " + unit : s;
    }
    return unit ? String(val) + " " + unit : String(val);
  }

  function formatBounds(bounds, unit) {
    if (!bounds || typeof bounds !== "object") {
      return "";
    }
    var parts = [];
    if (bounds.minimum !== undefined && bounds.minimum !== null && bounds.maximum !== undefined && bounds.maximum !== null) {
      parts.push("[" + formatNumber(bounds.minimum, "") + ", " + formatNumber(bounds.maximum, unit) + "]");
    } else {
      if (bounds.minimum !== undefined && bounds.minimum !== null) {
        parts.push("≥ " + formatNumber(bounds.minimum, unit));
      }
      if (bounds.maximum !== undefined && bounds.maximum !== null) {
        parts.push("≤ " + formatNumber(bounds.maximum, unit));
      }
    }
    if (bounds.target_value !== undefined && bounds.target_value !== null) {
      var tol = bounds.tolerance !== undefined ? bounds.tolerance : bounds.tolerance_abs;
      if (tol !== undefined && tol !== null) {
        parts.push(formatNumber(bounds.target_value, "") + " ± " + formatNumber(tol, unit));
      } else {
        parts.push("= " + formatNumber(bounds.target_value, unit));
      }
    }
    return parts.join(" · ");
  }

  function rgbCss(rgb) {
    if (!Array.isArray(rgb) || rgb.length < 3) {
      return "#0284c7";
    }
    var r = Math.max(0, Math.min(255, Math.round(rgb[0] <= 1 ? rgb[0] * 255 : rgb[0])));
    var g = Math.max(0, Math.min(255, Math.round(rgb[1] <= 1 ? rgb[1] * 255 : rgb[1])));
    var b = Math.max(0, Math.min(255, Math.round(rgb[2] <= 1 ? rgb[2] * 255 : rgb[2])));
    return "rgb(" + r + ", " + g + ", " + b + ")";
  }

  function channelColor(name, idx) {
    if (CHANNEL_COLORS[name]) {
      return CHANNEL_COLORS[name];
    }
    return FALLBACK_COLORS[idx % FALLBACK_COLORS.length];
  }

  function channelLabel(code) {
    var map = (STRINGS[lang] && STRINGS[lang].channels) || STRINGS.en.channels;
    return map[code] || code;
  }

  function assessmentLabel(code) {
    var map = (STRINGS[lang] && STRINGS[lang].assessments) || STRINGS.en.assessments;
    return map[code] || code || "—";
  }

  function assessmentTone(code) {
    if (code === "MET") return "ok";
    if (code === "UNMET") return "err";
    if (code === "INDETERMINATE" || code === "STALE_RUN") return "warn";
    return "";
  }

  function reqOutcomeLabel(code) {
    var map = (STRINGS[lang] && STRINGS[lang].reqOutcomes) || STRINGS.en.reqOutcomes;
    return map[code] || code || "—";
  }

  function qtyStateLabel(code) {
    var map = (STRINGS[lang] && STRINGS[lang].qtyStates) || STRINGS.en.qtyStates;
    return map[code] || code || "—";
  }

  function qtyStateTone(code) {
    if (code === "KNOWN" || code === "KNOWN_ZERO") return "ok";
    if (code === "BOUNDED" || code === "UNKNOWN") return "warn";
    return "";
  }

  function formatOrigin(branchKind) {
    if (!branchKind) {
      return t("primaryOrigin");
    }
    var dict = (STRINGS[lang] && STRINGS[lang].origins) || STRINGS.en.origins || {};
    if (dict[branchKind]) {
      return dict[branchKind];
    }
    return String(branchKind).replace(/_/g, " ");
  }

  function syncIframeLang(frame, clickButton) {
    var targetLang = lang === "zh" ? "zh-CN" : "en";
    try {
      window.localStorage.setItem("opensci-viewer-lang", targetLang);
    } catch (_e) {
      // ignore storage restrictions
    }
    if (!clickButton || !frame) {
      return;
    }
    try {
      var doc = frame.contentDocument;
      if (!doc) {
        return;
      }
      var langBtn = doc.getElementById("lang");
      if (langBtn) {
        var txt = (langBtn.textContent || "").trim();
        if ((lang === "en" && txt === "English") || (lang === "zh" && txt === "中文")) {
          langBtn.click();
        }
      }
    } catch (_e) {
      // ignore cross-document access error
    }
  }

  // A hidden pill carries no text and no tone: what a reader of the DOM (an agent, a screen reader, a text search)
  // finds is what the page shows, never the label of an earlier snapshot.
  function setPill(id, label, tone) {
    var node = el(id);
    if (!label) {
      node.textContent = "";
      node.className = "pill hidden";
      node.setAttribute("aria-hidden", "true");
      return;
    }
    node.textContent = label;
    node.className = "pill" + (tone ? " " + tone : "");
    node.removeAttribute("aria-hidden");
  }

  // The "syncing" overlay follows the same rule as the pills: hidden means empty.
  function showUpdating(text) {
    var node = el("frame-updating");
    node.textContent = text;
    node.classList.remove("hidden");
    node.removeAttribute("aria-hidden");
  }

  function hideUpdating() {
    var node = el("frame-updating");
    node.textContent = "";
    node.classList.add("hidden");
    node.setAttribute("aria-hidden", "true");
  }

  function renderLiveBadge() {
    var node = el("badge-live");
    var txt = el("badge-live-text");
    var map = {
      live: { text: t("liveConnected"), tone: "ok" },
      connecting: { text: t("liveConnecting"), tone: "" },
      reconnecting: { text: t("liveReconnecting"), tone: "warn" },
      polling: { text: t("livePolling"), tone: "warn" },
      disconnected: { text: t("liveDisconnected"), tone: "err" }
    };
    var cfg = map[liveState] || map.connecting;
    txt.textContent = cfg.text;
    node.className = "pill pill-live" + (cfg.tone ? " " + cfg.tone : "");
  }

  function applyStaticLabels() {
    document.documentElement.lang = lang === "zh" ? "zh-CN" : "en";
    el("app-title").textContent = t("appTitle");
    el("badge-readonly").textContent = t("readOnly");
    el("lbl-scene-title").textContent = t("sceneTitle");
    // The geometry-only / schematic badges and the syncing overlay are labelled where they are shown
    // (commitSnapshotPanel, showUpdating): a hidden one keeps no text.
    el("lbl-exec-title").textContent = t("execTitle");
    el("lbl-power-title").textContent = t("powerTitle");
    el("lbl-targets-title").textContent = t("targetsTitle");
    el("lbl-quantities-title").textContent = t("quantitiesTitle");
    el("lbl-branches-title").textContent = t("branchesTitle");
    el("lbl-details-title").textContent = t("detailsTitle");
    el("btn-lang-en").classList.toggle("active", lang === "en");
    el("btn-lang-zh").classList.toggle("active", lang === "zh");
    renderLiveBadge();
  }

  function renderBannerError(envelope) {
    var box = el("banner-error");
    clear(box);
    var appEl = el("app");
    if (envelope && typeof envelope === "object") {
      appEl.setAttribute("data-status", String(envelope.status || ""));
      if (typeof envelope.state_seq === "number") {
        appEl.setAttribute("data-state-seq", String(envelope.state_seq));
      }
    }
    if (!envelope || envelope.status === "ok" || !envelope.error) {
      box.className = "banner hidden";
      return;
    }
    var isErr = envelope.status === "error";
    box.className = "banner " + (isErr ? "banner-error" : "banner-warning");
    var main = h("div", "banner-main");
    main.appendChild(h("span", "banner-tag", isErr ? t("errorTag") : t("waitingTag")));
    var msgText = envelope.error.message || envelope.error.code || "";
    var heldSnap = displayedSnapshot || envelope.last_valid_snapshot;
    if (heldSnap) {
      var holdKey = envelope.error.code === "RUN_INTEGRITY_FAILED" ? "waitingIntegrityHolding" : "waitingHolding";
      msgText = t(holdKey, { rev: heldSnap.revision });
    }
    main.appendChild(h("span", "", msgText));
    box.appendChild(main);
    if (envelope.error.code) {
      var codeSpan = h("span", "banner-code", envelope.error.code);
      codeSpan.title = envelope.error.message || "";
      box.appendChild(codeSpan);
    }
  }

  function renderNotices(snap) {
    var box = el("banner-notices");
    clear(box);
    var notices = snap.notices || [];
    notices.forEach(function (n) {
      var cls = n.level === "error" ? "banner-error" : (n.level === "warning" ? "banner-warning" : "banner-info");
      var tagText = n.level === "error" ? t("noticeTagErr") : (n.level === "warning" ? t("noticeTagWarn") : t("noticeTagInfo"));
      var row = h("div", "banner " + cls);
      var main = h("div", "banner-main");
      main.appendChild(h("span", "banner-tag", tagText));

      var dictNotices = (STRINGS[lang] && STRINGS[lang].notices) || {};
      var localized = dictNotices[n.code];
      var displayMsg = n.message;
      if (localized) {
        var reasons = "";
        if (n.code === "STALE_RUN_MISMATCH") {
          reasons = ((snap.freshness && snap.freshness.reasons) || []).join(", ") || "PROJECT_CHANGED";
        } else if (n.code === "EXECUTION_PARTIAL") {
          reasons = ((snap.execution && snap.execution.budgets && snap.execution.budgets.exhausted) || []).join(", ") || "budget";
        }
        var parts = ((snap.scene && snap.scene.schematic_part_ids) || []).join(", ") || "—";
        displayMsg = localized.replace("{reasons}", reasons).replace("{parts}", parts);
      }
      main.appendChild(h("span", "", displayMsg));
      row.appendChild(main);
      if (n.code) {
        row.appendChild(h("span", "banner-code", n.code));
      }
      box.appendChild(row);
    });
  }

  function makeKpiTile(label, value, sub, tone) {
    var tile = h("div", "kpi-tile" + (tone ? " " + tone : ""));
    tile.appendChild(h("div", "kpi-label", label));
    var v = h("div", "kpi-value", value);
    v.title = value;
    tile.appendChild(v);
    if (sub) {
      var s = h("div", "kpi-sub", sub);
      s.title = sub;
      tile.appendChild(s);
    }
    return tile;
  }

  function renderExecutionCard(snap) {
    var kpis = el("execution-kpis");
    var alerts = el("execution-alerts");
    clear(kpis);
    clear(alerts);

    var exec = snap.execution;
    var integ = snap.integrity || {};
    var fresh = snap.freshness || {};
    var tsum = snap.target_summary || {};

    el("exec-subtitle").textContent = (snap.identity && snap.identity.engine_version)
      ? ("os-optics " + snap.identity.engine_version)
      : "";

    var execVal = t("notComputed");
    var execSub = snap.result_mode === "stale_run" ? t("freshHistorical") : t("geomOnlyBadge");
    var execTone = snap.result_mode === "stale_run" ? "warn" : "info";
    if (exec) {
      if (exec.outcome === "COMPLETED") {
        execVal = t("completeLabel");
        execTone = "ok";
        execSub = exec.in_model_scope === false ? t("outOfScope") : t("inScope");
      } else if (exec.outcome === "PARTIAL") {
        execVal = t("partialLabel");
        execTone = "warn";
        execSub = exec.in_model_scope === false ? t("outOfScope") : t("partialSub");
      } else {
        execVal = exec.outcome || t("failedLabel");
        execTone = "err";
        execSub = exec.in_model_scope === false ? t("outOfScope") : t("inScope");
      }
    }
    kpis.appendChild(makeKpiTile(t("kpiExecution"), execVal, execSub, execTone));

    var reqCode = (exec && exec.requirement_outcome) || tsum.requirement_outcome || "NO_TARGETS";
    var reqVal = reqOutcomeLabel(reqCode);
    var reqTone = "";
    if (reqCode === "PASS") reqTone = "ok";
    else if (reqCode === "FAIL") reqTone = "err";
    else if (reqCode === "UNKNOWN" || reqCode === "INDETERMINATE" || reqCode === "STALE_RUN") reqTone = "warn";
    var reqSub = tsum.declared
      ? ((tsum.met ? tsum.met.length : 0) + "/" + (snap.targets ? snap.targets.length : 0) + " " + assessmentLabel("MET"))
      : t("noTargets");
    if (!snap.is_computed_result && tsum.declared) {
      reqSub = t("targetsCountSub", { n: snap.targets ? snap.targets.length : 0 });
    }
    kpis.appendChild(makeKpiTile(t("kpiRequirements"), reqVal, reqSub, reqTone));

    var integVal = integ.checked ? (integ.consistent ? t("consistent") : t("integrityFailed")) : t("notChecked");
    var freshText = t("freshNoRun");
    if (fresh.state === "CURRENT") freshText = t("freshCurrent");
    else if (fresh.state === "HISTORICAL") freshText = t("freshHistorical");
    else if (fresh.state === "UNDETERMINED") freshText = t("freshUndetermined");
    else if (fresh.state === "RUN_ONLY") freshText = t("freshRunOnly");

    var intTone = "";
    if (integ.checked && !integ.consistent) intTone = "err";
    else if (fresh.state === "HISTORICAL" || fresh.state === "UNDETERMINED") intTone = "warn";
    else if (integ.consistent && (fresh.state === "CURRENT" || fresh.state === "RUN_ONLY")) intTone = "ok";
    kpis.appendChild(makeKpiTile(t("kpiIntegrity"), integVal, freshText, intTone));

    if (exec && exec.budgets && exec.budgets.exhausted && exec.budgets.exhausted.length) {
      alerts.appendChild(h("div", "inline-alert", t("exhaustedBudgets", { list: exec.budgets.exhausted.join(", ") })));
    }
    if (snap.project_check && snap.project_check.checked && snap.project_check.ok === false) {
      var pCount = (snap.project_check.problems || []).length || 1;
      alerts.appendChild(h("div", "inline-alert err", t("validationProblems", { count: pCount })));
    }
  }

  function renderPowerLedger(snap) {
    var body = el("power-body");
    clear(body);

    var power = snap.power_ledger || {};
    if (!power.available) {
      // Withheld ledger (stale run, no run, configuration not in the run): no conserved/residual verdict either.
      setPill("badge-conserved", "", "");
      var isStale = power.reason === "STALE_RUN" || snap.result_mode === "stale_run";
      var box = h("div", "empty-state" + (isStale ? " warn" : ""));
      var msg = power.reason === "NO_RUN"
        ? t("powerUnavailableNoRun")
        : (power.reason === "CONFIGURATION_NOT_IN_RUN"
          ? t("powerUnavailableConfigNotInRun")
          : (isStale ? t("powerUnavailableStale") : t("powerUnavailableOther")));
      box.appendChild(h("div", "", msg));
      if (power.reason === "NO_RUN" && snap.source && snap.source.input_path) {
        var fileName = snap.source.input_path.split("/").pop() || "project.json";
        box.appendChild(h("div", "subline", t("tryEvaluateHint")));
        box.appendChild(h("code", "empty-cmd", "os-optics-kit view " + fileName + " --evaluate"));
      }
      body.appendChild(box);
      return;
    }

    if (power.conserved === true) {
      setPill("badge-conserved", t("conservedYes"), "ok");
    } else if (power.conserved === false) {
      setPill("badge-conserved", t("conservedNo"), "warn");
    } else {
      setPill("badge-conserved", "", "");
    }

    var hero = h("div", "power-hero");
    var sIn = h("div", "power-stat");
    sIn.appendChild(h("div", "power-stat-label", t("totalInput")));
    var vIn = h("div", "power-stat-val", formatPower(power.total_input_W));
    vIn.title = formatExactWatts(power.total_input_W);
    sIn.appendChild(vIn);
    hero.appendChild(sIn);

    var sRes = h("div", "power-stat");
    sRes.appendChild(h("div", "power-stat-label", t("residualPower")));
    var vRes = h("div", "power-stat-val", formatPower(power.residual_W));
    vRes.title = formatExactWatts(power.residual_W);
    sRes.appendChild(vRes);
    hero.appendChild(sRes);
    body.appendChild(hero);

    var channels = (power.channels || []).slice().sort(function (a, b) {
      return (b.power_W || 0) - (a.power_W || 0);
    });
    if (channels.length) {
      var track = h("div", "power-bar-track");
      channels.forEach(function (c, idx) {
        var frac = c.fraction_of_input;
        if (frac === null || frac === undefined || frac <= 0) {
          return;
        }
        var seg = h("div", "power-bar-seg");
        seg.style.width = Math.max(frac * 100, 0.8).toFixed(3) + "%";
        seg.style.background = channelColor(c.channel, idx);
        seg.title = channelLabel(c.channel) + ": " + formatPower(c.power_W) + " (" + (frac * 100).toFixed(2) + "%)";
        track.appendChild(seg);
      });
      body.appendChild(track);
    }

    var wrap = h("div", "table-wrap");
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    trh.appendChild(h("th", "", t("colChannel")));
    trh.appendChild(h("th", "num", t("colPower")));
    trh.appendChild(h("th", "num", t("colShare")));
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    channels.forEach(function (c, idx) {
      var tr = document.createElement("tr");
      var tdCh = document.createElement("td");
      var chWrap = h("div", "channel-cell");
      var dot = h("span", "swatch");
      dot.style.background = channelColor(c.channel, idx);
      chWrap.appendChild(dot);
      var txtWrap = h("div");
      txtWrap.appendChild(h("span", "", channelLabel(c.channel)));
      if (channelLabel(c.channel) !== c.channel) {
        txtWrap.appendChild(h("span", "channel-sub", c.channel));
      }
      chWrap.appendChild(txtWrap);
      tdCh.appendChild(chWrap);
      tr.appendChild(tdCh);

      var tdPow = h("td", "num", formatPower(c.power_W));
      tdPow.title = formatExactWatts(c.power_W);
      tr.appendChild(tdPow);

      var pct = (c.fraction_of_input !== null && c.fraction_of_input !== undefined)
        ? (c.fraction_of_input * 100).toFixed(2) + "%"
        : "—";
      tr.appendChild(h("td", "num", pct));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);

    var byEl = power.by_element || [];
    if (byEl.length) {
      var det = h("details", "sub-details");
      det.appendChild(h("summary", "", t("byElementSummary") + " (" + byEl.length + ")"));
      var eWrap = h("div", "table-wrap");
      var eTable = document.createElement("table");
      var eThead = document.createElement("thead");
      var eTrh = document.createElement("tr");
      eTrh.appendChild(h("th", "", t("colElement")));
      eTrh.appendChild(h("th", "", t("colChannel")));
      eTrh.appendChild(h("th", "num", t("colPower")));
      eThead.appendChild(eTrh);
      eTable.appendChild(eThead);
      var eTbody = document.createElement("tbody");
      byEl.forEach(function (row) {
        var tr = document.createElement("tr");
        var elemId = row.element_id || row.surface_id || row.instance_id || "—";
        tr.appendChild(h("td", "mono", elemId));
        tr.appendChild(h("td", "", channelLabel(row.channel || row.sink || "—")));
        var pw = row.power_W !== undefined ? row.power_W : row.total_power_W;
        var tdP = h("td", "num", formatPower(pw));
        tdP.title = formatExactWatts(pw);
        tr.appendChild(tdP);
        eTbody.appendChild(tr);
      });
      eTable.appendChild(eTbody);
      eWrap.appendChild(eTable);
      det.appendChild(eWrap);
      body.appendChild(det);
    }
  }

  function renderTargets(snap) {
    var body = el("targets-body");
    var summaryPills = el("targets-summary-pills");
    clear(body);
    clear(summaryPills);

    var targets = snap.targets || [];
    var tsum = snap.target_summary || {};
    if (snap.is_computed_result && tsum.declared) {
      var metN = (tsum.met || []).length;
      var unmetN = (tsum.unmet || []).length;
      var indN = (tsum.indeterminate || []).length;
      if (metN) summaryPills.appendChild(h("span", "pill ok", metN + " " + assessmentLabel("MET")));
      if (unmetN) summaryPills.appendChild(h("span", "pill err", unmetN + " " + assessmentLabel("UNMET")));
      if (indN) summaryPills.appendChild(h("span", "pill warn", indN + " " + assessmentLabel("INDETERMINATE")));
    }

    if (!targets.length) {
      body.appendChild(h("div", "muted", t("noTargets")));
      return;
    }

    var wrap = h("div", "table-wrap");
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    trh.appendChild(h("th", "", t("colTarget")));
    trh.appendChild(h("th", "", t("colAssessment")));
    trh.appendChild(h("th", "num", t("colValue")));
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    targets.forEach(function (row) {
      var tr = document.createElement("tr");

      var tdId = document.createElement("td");
      tdId.appendChild(h("div", "mono", row.target_id || "—"));
      var metaParts = [];
      if (row.metric) metaParts.push(row.metric);
      if (row.observation_id) metaParts.push("@" + row.observation_id);
      if (metaParts.length) {
        tdId.appendChild(h("span", "subline", metaParts.join(" · ")));
      }
      tr.appendChild(tdId);

      var tdAss = document.createElement("td");
      var code = row.assessment || row.status || "NOT_COMPUTED";
      tdAss.appendChild(h("span", "pill " + assessmentTone(code), assessmentLabel(code)));
      if (row.quantity_state && row.quantity_state !== "KNOWN" && row.quantity_state !== "UNCOMPUTED") {
        tdAss.appendChild(h("span", "subline", qtyStateLabel(row.quantity_state)));
      }
      tr.appendChild(tdAss);

      var tdVal = h("td", "num");
      tdVal.appendChild(h("div", "", row.value !== null && row.value !== undefined ? formatNumber(row.value, row.unit) : "—"));
      var bStr = formatBounds(row.bounds, row.unit);
      if (bStr) {
        tdVal.appendChild(h("span", "subline", bStr));
      }
      tr.appendChild(tdVal);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  function renderQuantities(snap) {
    var body = el("quantities-body");
    clear(body);
    var quantities = snap.quantities || [];
    el("quantities-count").textContent = quantities.length ? String(quantities.length) : "";

    if (!quantities.length) {
      body.appendChild(h("div", "muted", t("noQuantities")));
      return;
    }

    var wrap = h("div", "table-wrap");
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    trh.appendChild(h("th", "", t("colPlane")));
    trh.appendChild(h("th", "", t("colState")));
    trh.appendChild(h("th", "num", t("colTotalPower")));
    trh.appendChild(h("th", "num", t("colCompleteness")));
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    quantities.forEach(function (q) {
      var tr = document.createElement("tr");
      tr.appendChild(h("td", "mono", q.observation_id || "—"));

      var tdState = document.createElement("td");
      var qState = q.quantity_state || q.status || "UNCOMPUTED";
      tdState.appendChild(h("span", "pill " + qtyStateTone(qState), qtyStateLabel(qState)));
      tr.appendChild(tdState);

      var tdPow = h("td", "num");
      tdPow.appendChild(h("div", "", formatPower(q.total_power_W)));
      if (q.total_power_W !== null && q.total_power_W !== undefined) {
        tdPow.title = formatExactWatts(q.total_power_W);
      }
      if (!q.power_complete && q.power_upper_bound_W !== null && q.power_upper_bound_W !== undefined) {
        tdPow.appendChild(h("span", "subline", t("upperBoundFmt", { val: formatPower(q.power_upper_bound_W) })));
      }
      tr.appendChild(tdPow);

      var tdComp = h("td", "num");
      if (!snap.is_computed_result) {
        tdComp.appendChild(h("span", "muted", t("uncomputedTag")));
      } else if (q.power_complete) {
        tdComp.appendChild(h("span", "", t("completeTag")));
      } else {
        tdComp.appendChild(h("span", "", qState === "BOUNDED" ? t("boundedTag") : t("incompleteTag")));
        if (q.untraced_power_W !== null && q.untraced_power_W !== undefined && q.untraced_power_W > 0) {
          tdComp.appendChild(h("span", "subline", t("untracedFmt", { val: formatPower(q.untraced_power_W) })));
        }
      }
      tr.appendChild(tdComp);

      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  function renderBranches(snap) {
    var body = el("branches-body");
    var chips = el("scene-branch-chips");
    clear(body);
    clear(chips);

    var branches = snap.branches || [];
    el("branches-count").textContent = branches.length ? String(branches.length) : "";

    if (!branches.length) {
      body.appendChild(h("div", "muted", snap.is_computed_result ? t("noBranchesComputed") : t("noBranchesPreview")));
      chips.appendChild(h("span", "muted", snap.is_computed_result ? t("noBranchesComputed") : t("noRaysPreview")));
      return;
    }

    branches.slice(0, 6).forEach(function (b) {
      var chip = h("span", "branch-chip");
      var dot = h("span", "swatch");
      dot.style.background = rgbCss(b.display_rgb);
      chip.appendChild(dot);
      chip.appendChild(h("span", "branch-chip-id", b.path_id || "—"));
      chip.appendChild(h("span", "", formatPower(b.final_power_W)));
      if (b.termination) {
        chip.appendChild(h("span", "muted", "· " + channelLabel(b.termination)));
      }
      chips.appendChild(chip);
    });
    if (branches.length > 6) {
      chips.appendChild(h("span", "muted", "+" + (branches.length - 6)));
    }

    var wrap = h("div", "table-wrap");
    var table = document.createElement("table");
    var thead = document.createElement("thead");
    var trh = document.createElement("tr");
    trh.appendChild(h("th", "", t("colBranch")));
    trh.appendChild(h("th", "", t("colOrigin")));
    trh.appendChild(h("th", "num", t("colFinalPower")));
    trh.appendChild(h("th", "", t("colTermination")));
    thead.appendChild(trh);
    table.appendChild(thead);

    var tbody = document.createElement("tbody");
    branches.forEach(function (b) {
      var tr = document.createElement("tr");

      var tdId = document.createElement("td");
      var idWrap = h("div", "channel-cell");
      var dot = h("span", "swatch");
      dot.style.background = rgbCss(b.display_rgb);
      idWrap.appendChild(dot);
      var idTxt = h("div");
      idTxt.appendChild(h("span", "mono", b.path_id || "—"));
      if (b.wavelength_nm !== null && b.wavelength_nm !== undefined) {
        idTxt.appendChild(h("span", "subline", formatNumber(b.wavelength_nm, "nm")));
      }
      idWrap.appendChild(idTxt);
      tdId.appendChild(idWrap);
      tr.appendChild(tdId);

      tr.appendChild(h("td", "", formatOrigin(b.branch_kind)));

      var tdPow = h("td", "num", formatPower(b.final_power_W));
      tdPow.title = formatExactWatts(b.final_power_W);
      tr.appendChild(tdPow);

      tr.appendChild(h("td", "", b.termination ? channelLabel(b.termination) : "—"));
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);
    body.appendChild(wrap);
  }

  function renderDetails(snap, envelope) {
    var body = el("details-body");
    clear(body);

    var src = snap.source || {};
    var id = snap.identity || {};
    var scene = snap.scene || {};
    var evalInfo = src.evaluation || {};
    el("details-hint").textContent = shortHash(id.project_hash || snap.snapshot_id);

    var kv = h("div", "kv-list");
    var pairs = [
      [t("detailSnapshotId"), snap.snapshot_id || "—"],
      [t("detailInputPath"), src.input_path || "—"],
      [t("detailProjectPath"), src.project_path || "—"],
      [t("detailRunDir"), src.run_dir || "—"]
    ];
    if (src.root_run_dir && src.root_run_dir !== src.run_dir) {
      pairs.push([t("detailRootRunDir"), src.root_run_dir]);
    }
    if (src.configuration_id) {
      var cfgText = src.configuration_id;
      if (src.available_configurations && src.available_configurations.length) {
        cfgText += " (" + src.available_configurations.join(", ") + ")";
      }
      pairs.push([t("detailConfig"), cfgText]);
    }
    pairs.push([t("detailEvalMode"), evalInfo.mode === "explicit_local_temp" ? t("evalModeTemp") : t("evalModeNone")]);
    if (scene.ray_data_mode) {
      var crossingInfo = scene.crossing_table_available
        ? (scene.ray_data_mode + " (" + scene.crossing_row_count + " " + t("crossingsUnit") + ")")
        : (scene.ray_data_mode + " (" + t("summaryRaysTag") + ")");
      pairs.push([t("detailRayDataMode"), crossingInfo]);
    }
    if (scene.geometry_sources && scene.geometry_sources.length) {
      pairs.push([t("detailGeometrySources"), scene.geometry_sources.join(", ")]);
    }
    if (scene.schematic_part_ids && scene.schematic_part_ids.length) {
      pairs.push([t("detailSchematicParts"), scene.schematic_part_ids.join(", ")]);
    }
    if (scene.view_placeholder_part_ids && scene.view_placeholder_part_ids.length) {
      pairs.push([t("detailPlaceholderParts"), scene.view_placeholder_part_ids.join(", ")]);
    }
    pairs.push([t("detailProjectHash"), id.project_hash || "—"]);
    if (id.root_project_hash && id.root_project_hash !== id.project_hash) {
      pairs.push([t("detailRootProjectHash"), id.root_project_hash]);
    }
    pairs.push([t("detailInputHash"), id.engine_input_hash || "—"]);
    pairs.push([t("detailResultHash"), id.result_hash || "—"]);
    pairs.push([t("detailSceneHash"), id.scene_content_hash || "—"]);
    pairs.push([t("detailVersions"), "kit " + (id.kit_version || "—") + " · os-optics " + (id.engine_version || "—")]);

    pairs.forEach(function (pair) {
      kv.appendChild(h("div", "kv-key", pair[0]));
      kv.appendChild(h("div", "kv-val", pair[1]));
    });
    body.appendChild(kv);

    if (snap.historical_run) {
      var hist = snap.historical_run;
      var hBox = h("div", "historical-box");
      hBox.appendChild(h("h4", "", t("historicalTitle")));
      var hKv = h("div", "kv-list");
      [
        [t("detailRunDir"), hist.run_dir || "—"],
        [t("detailProjectHash"), hist.project_hash || "—"],
        [t("detailInputHash"), hist.engine_input_hash || "—"],
        ["Reasons", (hist.freshness_reasons || []).join(", ") || "—"]
      ].forEach(function (pair) {
        hKv.appendChild(h("div", "kv-key", pair[0]));
        hKv.appendChild(h("div", "kv-val", pair[1]));
      });
      hBox.appendChild(hKv);
      body.appendChild(hBox);
    }

    if (snap.project_check && snap.project_check.problems && snap.project_check.problems.length) {
      var pBox = h("div", "historical-box");
      pBox.appendChild(h("h4", "", "Project Validation Problems"));
      snap.project_check.problems.forEach(function (prob) {
        var line = typeof prob === "string" ? prob : JSON.stringify(prob);
        pBox.appendChild(h("div", "mono", line));
      });
      body.appendChild(pBox);
    }

    var stampParts = ["Rev #" + snap.revision];
    if (id.project_hash) stampParts.push("proj " + shortHash(id.project_hash));
    if (id.result_hash) stampParts.push("res " + shortHash(id.result_hash));
    if (id.scene_content_hash) stampParts.push("scene " + shortHash(id.scene_content_hash));
    if (envelope && envelope.server && envelope.server.engine_version) {
      stampParts.push("os-optics " + envelope.server.engine_version);
    }
    el("binding-stamp").textContent = stampParts.join(" · ");
  }

  function formatSceneMeta(scene) {
    var branchCount = scene.branch_count !== undefined && scene.branch_count !== null
      ? scene.branch_count
      : (scene.beam_count || 0);
    var drawableCount = scene.drawable_ray_count !== undefined && scene.drawable_ray_count !== null
      ? scene.drawable_ray_count
      : (scene.ray_count || 0);
    var parts = [
      (scene.part_count || 0) + " " + t("partsUnit"),
      branchCount + " " + t("branchesUnit"),
      drawableCount + " " + t("drawableRaysUnit")
    ];
    if (!scene.geometry_only) {
      if (scene.crossing_table_available && scene.crossing_row_count !== null && scene.crossing_row_count !== undefined) {
        parts.push(scene.crossing_row_count + " " + t("crossingsUnit"));
      } else if (scene.ray_data_mode === "summary") {
        parts.push(t("summaryRaysTag"));
      }
    }
    return parts.join(" · ");
  }

  function boolAttr(value) {
    return value === true ? "true" : (value === false ? "false" : "");
  }

  // Machine-readable state of the shown snapshot on #app, each attribute the value of the same field of /api/snapshot
  // ("" where that field is null or absent), so an agent reads what the page shows without parsing localized text.
  function setStateAttributes(appEl, snap) {
    var s = snap || {};
    var fresh = s.freshness || {};
    var power = s.power_ledger || {};
    var scene = s.scene || {};
    var exec = s.execution || {};
    var identity = s.identity || {};
    var attrs = {
      "data-result-mode": s.result_mode || "",
      "data-computed": snap ? boolAttr(s.is_computed_result === true) : "",
      "data-freshness": fresh.checked ? String(fresh.state || "") : "",
      "data-project-hash": identity.project_hash || "",
      "data-execution-outcome": exec.outcome || "",
      "data-requirement-outcome": exec.requirement_outcome || ((s.target_summary || {}).requirement_outcome) || "",
      "data-power-available": snap ? boolAttr(power.available === true) : "",
      "data-power-conserved": power.available ? boolAttr(power.conserved) : "",
      "data-branch-count": snap ? String((s.branches || []).length) : "",
      "data-geometry-only": snap ? boolAttr(scene.geometry_only === true) : "",
      "data-drawable-ray-count": (snap && typeof scene.drawable_ray_count === "number") ? String(scene.drawable_ray_count) : ""
    };
    Object.keys(attrs).forEach(function (name) {
      appEl.setAttribute(name, attrs[name]);
    });
  }

  function commitSnapshotPanel(snap) {
    displayedSnapshot = snap;
    displayedSnapshotId = snap ? (snap.snapshot_id || null) : null;

    var appEl = el("app");
    appEl.setAttribute("data-revision", String(snap ? snap.revision : (latestStatusEnvelope ? (latestStatusEnvelope.revision || 0) : 0)));
    appEl.setAttribute("data-snapshot-id", displayedSnapshotId || "");

    setPill("badge-rev", "Rev #" + (snap ? snap.revision : (latestStatusEnvelope ? (latestStatusEnvelope.revision || 0) : 0)), "pill-mono");
    setStateAttributes(appEl, snap);

    if (!snap) {
      setPill("badge-mode", t("modeNone"), "warn");
      setPill("badge-freshness", "", "");
      setPill("badge-geom-only", "", "");
      setPill("badge-schematic", "", "");
      renderBannerError(latestStatusEnvelope);
      return;
    }

    if (snap.result_mode === "computed_run") {
      setPill("badge-mode", t("modeComputed"), "ok");
    } else if (snap.result_mode === "stale_run") {
      setPill("badge-mode", t("modeStale"), "warn");
    } else {
      setPill("badge-mode", t("modePreview"), "info");
    }

    var fresh = snap.freshness || {};
    if (fresh.checked) {
      var fLabel = fresh.state === "CURRENT" ? t("freshCurrent")
        : (fresh.state === "HISTORICAL" ? t("freshHistorical") : t("freshUndetermined"));
      setPill("badge-freshness", fLabel, fresh.state === "CURRENT" ? "ok" : "warn");
    } else {
      setPill("badge-freshness", "", "");
    }

    var rawPath = (snap.source && snap.source.input_path) || "";
    var srcEl = el("source-path");
    srcEl.title = rawPath;
    var segs = rawPath.split("/").filter(Boolean);
    srcEl.textContent = segs.length > 2 ? "…/" + segs.slice(-2).join("/") : rawPath;

    renderNotices(snap);

    var scene = snap.scene || {};
    setPill("badge-geom-only", scene.geometry_only ? t("geomOnlyBadge") : "", "pill-info");
    setPill("badge-schematic", scene.has_schematic_geometry ? t("schematicBadge") : "", "pill-warn");
    el("scene-meta").textContent = formatSceneMeta(scene);

    renderExecutionCard(snap);
    renderPowerLedger(snap);
    renderTargets(snap);
    renderQuantities(snap);
    renderBranches(snap);
    renderDetails(snap, latestStatusEnvelope);
    // Always apply status banner from latestStatusEnvelope (never from a historical staged envelope!).
    renderBannerError(latestStatusEnvelope);
  }

  function stopPendingPoll() {
    if (pendingPollTimer !== null) {
      window.clearInterval(pendingPollTimer);
      pendingPollTimer = null;
    }
  }

  function scheduleFreshRetry() {
    if (retryFetchTimer !== null) {
      return;
    }
    var delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * Math.pow(2, Math.max(0, abandonedFrames - 1)));
    retryFetchTimer = window.setTimeout(function () {
      retryFetchTimer = null;
      fetchSnapshot();
    }, delay);
  }

  function pumpQueuedSnapshot() {
    if (stagingSnapshot !== null || queuedSnapshot === null) {
      return;
    }
    var next = queuedSnapshot;
    queuedSnapshot = null;
    if (next.snapshot_id === displayedSnapshotId) {
      hideUpdating();
      commitSnapshotPanel(next);
      return;
    }
    startStaging(next);
  }

  // ---------------------------------------------------------------------------------------------- camera carry-over
  //
  // Every snapshot is drawn in a fresh engine frame, and the engine page always starts on its own "iso" preset, which
  // it derives from the scene's beams and fit box: a geometry-only view (stale run, no run) and a traced view of the
  // same layout therefore start from different cameras. The page instead keeps the camera the reader is looking
  // through: at each swap the outgoing frame's camera (projection, eye, target, screen up, extent) is applied to the
  // incoming frame before it becomes visible. Fit, Fit all, Reset and the view buttons still reframe on request. The
  // first frame keeps the engine's own framing. Everything read here is guarded: a frame without a working viewer
  // falls back to the engine's default view.
  function vecArray(v) {
    return (v && typeof v.toArray === "function") ? v.toArray() : null;
  }

  function finiteArray(a, n) {
    if (!a || a.length !== n) {
      return false;
    }
    for (var i = 0; i < n; i++) {
      if (typeof a[i] !== "number" || !isFinite(a[i])) {
        return false;
      }
    }
    return true;
  }

  function captureCameraPose(frame) {
    try {
      var win = frame && frame.contentWindow;
      var v = win && win.__opensciViewer;
      if (!v || !v.camera || !v.controls || !v.controls.target) {
        return null;
      }
      var cam = v.camera;
      var pose = {
        orthographic: !!cam.isOrthographicCamera,
        position: vecArray(cam.position),
        target: vecArray(v.controls.target),
        up: vecArray(cam.up),
        near: cam.near,
        far: cam.far,
        zoom: cam.zoom,
        view: v.currentView || null,
        auto_frame: null
      };
      if (!finiteArray(pose.position, 3) || !finiteArray(pose.target, 3) || !finiteArray(pose.up, 3)) {
        return null;
      }
      if (pose.orthographic) {
        pose.top = cam.top;
        pose.bottom = cam.bottom;
        pose.left = cam.left;
        pose.right = cam.right;
      }
      var af = v.autoFrame;
      if (af && af.dir && af.up && af.box && af.box.min && af.box.max) {
        // Not moved by the reader since the view was framed: keep it framed on the same box when the area resizes.
        pose.auto_frame = {
          dir: vecArray(af.dir),
          up: vecArray(af.up),
          min: vecArray(af.box.min),
          max: vecArray(af.box.max),
          aim: af.aim ? vecArray(af.aim) : null
        };
      }
      return pose;
    } catch (_e) {
      return null;
    }
  }

  function applyCameraPose(frame, pose) {
    if (!pose) {
      return false;
    }
    try {
      var win = frame && frame.contentWindow;
      var v = win && win.__opensciViewer;
      if (!v || !v.perspective || !v.orthographic || !v.controls || typeof v.useCamera !== "function") {
        return false;
      }
      var cam = pose.orthographic ? v.orthographic : v.perspective;
      v.useCamera(cam);
      cam.up.fromArray(pose.up);
      // OrbitControls take their rotation basis from camera.up when created: new up (or camera) -> new controls.
      if (typeof v.makeControls === "function"
          && (!v.controlsUp || !cam.up.equals(v.controlsUp) || v.controls.object !== cam)) {
        v.makeControls();
      }
      v.controls.target.fromArray(pose.target);
      cam.position.fromArray(pose.position);
      if (pose.near > 0 && pose.far > pose.near) {
        cam.near = pose.near;
        cam.far = pose.far;
      }
      if (pose.orthographic && pose.top > 0) {
        cam.top = pose.top;
        cam.bottom = pose.bottom;
        cam.left = pose.left;
        cam.right = pose.right;
      }
      if (pose.zoom > 0) {
        cam.zoom = pose.zoom;
      }
      cam.updateProjectionMatrix();
      v.controls.update();
      v.autoFrame = null;
      var af = pose.auto_frame;
      if (af && finiteArray(af.dir, 3) && finiteArray(af.up, 3) && finiteArray(af.min, 3) && finiteArray(af.max, 3)
          && v.focusBox && typeof v.focusBox.clone === "function") {
        var box = v.focusBox.clone();
        box.min.fromArray(af.min);
        box.max.fromArray(af.max);
        v.autoFrame = {
          dir: cam.position.clone().fromArray(af.dir),
          up: cam.position.clone().fromArray(af.up),
          box: box,
          aim: finiteArray(af.aim, 3) ? cam.position.clone().fromArray(af.aim) : null
        };
      }
      v.currentView = pose.view;
      var doc = frame.contentDocument;
      if (doc) {
        doc.querySelectorAll("#views button, #planes button").forEach(function (b) {
          b.classList.toggle("active", !!pose.view && b.dataset.view === pose.view);
        });
      }
      if (typeof v.renderNow === "function") {
        v.renderNow();
      } else if (typeof v.requestRender === "function") {
        v.requestRender();
      }
      return true;
    } catch (_e) {
      return false;
    }
  }

  function startStaging(snap) {
    stopPendingPoll();
    var myToken = ++transitionToken;
    var expectedSnapshotId = snap.snapshot_id;
    stagingSnapshot = snap;
    var expectedSceneHash = (snap.scene && snap.scene.scene_content_hash) || (snap.identity && snap.identity.scene_content_hash) || null;
    var url = snap.scene && snap.scene.engine_view_url;

    if (!url) {
      stagingSnapshot = null;
      commitSnapshotPanel(snap);
      pumpQueuedSnapshot();
      return;
    }

    showUpdating(t("updatingRev", { rev: snap.revision }));

    var staging = getStagingFrame();
    var oldActive = getActiveFrame();
    syncIframeLang(staging, false);

    function finishCommit() {
      if (myToken !== transitionToken) {
        return;
      }
      stopPendingPoll();
      syncIframeLang(staging, true);

      // The reader's camera, read at the moment of the swap (an orbit made while this scene loaded is kept), is the
      // camera of the incoming frame's first visible picture.
      var carried = applyCameraPose(staging, captureCameraPose(oldActive));
      staging.setAttribute("data-camera", carried ? "carried" : "default");

      // Atomic synchronous swap: visible 3D scene AND right-hand panel flip together in one JS tick,
      // while #banner-error stays bound to latestStatusEnvelope (never cleared by a delayed valid scene!).
      staging.setAttribute("data-snapshot-id", expectedSnapshotId);
      staging.setAttribute("data-revision", String(snap.revision));
      staging.classList.remove("staging");
      staging.classList.add("active");
      staging.removeAttribute("aria-hidden");

      oldActive.classList.remove("active");
      oldActive.classList.add("staging");
      oldActive.setAttribute("aria-hidden", "true");
      activeFrameSlot = activeFrameSlot === "a" ? "b" : "a";

      stagingSnapshot = null;
      abandonedFrames = 0;
      commitSnapshotPanel(snap);

      if (queuedSnapshot !== null && queuedSnapshot.snapshot_id !== displayedSnapshotId) {
        pumpQueuedSnapshot();
      } else {
        queuedSnapshot = null;
        hideUpdating();
      }
    }

    // Every outcome other than a commit ends here, so no outcome keeps the single in-flight slot: never swap to this
    // frame; move on to the newer queued snapshot, or ask for the latest one again (after a growing pause while
    // frames keep failing). The visible frame and its panel stay as they are meanwhile.
    function abandonStaging() {
      if (myToken !== transitionToken) {
        return;
      }
      stopPendingPoll();
      stagingSnapshot = null;
      abandonedFrames += 1;
      if (queuedSnapshot !== null && queuedSnapshot.snapshot_id !== expectedSnapshotId) {
        pumpQueuedSnapshot();
      } else {
        queuedSnapshot = null;
        scheduleFreshRetry();
      }
    }

    // Runs only after the frame's load event, when its page and every script in it have loaded and run.
    function inspectStaging() {
      if (myToken !== transitionToken) {
        stopPendingPoll();
        return;
      }
      var win = null;
      var doc = null;
      try {
        win = staging.contentWindow;
        doc = staging.contentDocument;
      } catch (_e) {
        doc = null;
      }
      if (!win || !doc) {
        // A loaded frame whose document cannot be read is a browser error page: this scene never starts.
        abandonStaging();
        return;
      }
      try {
        // HTTP 409 JSON body returned for the page of an evicted /engine-view/<snapshot_id>/
        if (!doc.getElementById("stage") && doc.body && doc.body.textContent && doc.body.textContent.indexOf("SNAPSHOT_SUPERSEDED") !== -1) {
          abandonStaging();
          return;
        }
        // engine-boot.js (the first script of every engine page) records a script or style that failed to load and an
        // error raised while the engine started. Without its record this is not a working engine page at all.
        var boot = win.__osOpticsKitBoot;
        if (!boot || boot.failed) {
          abandonStaging();
          return;
        }
        // Engine viewer sets window.__opensciViewer ONLY after view.json finishes loading and viewer.init() renders!
        if (win.__opensciViewer) {
          var loadedDoc = win.__opensciViewer.doc;
          var loadedHash = loadedDoc && loadedDoc.identity && loadedDoc.identity.scene_content_hash;
          if (expectedSceneHash && loadedHash && loadedHash !== expectedSceneHash) {
            abandonStaging();
            return;
          }
          finishCommit();
          return;
        }
        // Engine viewer failed inside loadDocument() (e.g. view.json returned HTTP 409 SNAPSHOT_SUPERSEDED)
        var errNode = doc.getElementById("error");
        if (errNode) {
          var errText = errNode.textContent || "";
          if (errText.indexOf("409") !== -1 || errText.toLowerCase().indexOf("load") !== -1 || errText.indexOf("加载") !== -1) {
            abandonStaging();
            return;
          }
          // Non-superseded rendering error (e.g. WebGL unavailable): still commit panel coherently.
          finishCommit();
          return;
        }
        // Otherwise the engine started and its scene is still loading: keep waiting, however long a valid load takes.
      } catch (_e) {
        abandonStaging();
      }
    }

    staging.onload = function () {
      if (myToken !== transitionToken) {
        return;
      }
      inspectStaging();
      // Never abort a slow-but-valid scene load on an arbitrary timer; wait until __opensciViewer mounts or errors.
      if (myToken === transitionToken && stagingSnapshot !== null) {
        pendingPollTimer = window.setInterval(function () {
          if (myToken !== transitionToken) {
            stopPendingPoll();
            return;
          }
          inspectStaging();
        }, 35);
      }
    };

    staging.onerror = function () {
      abandonStaging();
    };

    staging.src = url;
  }

  function handleEnvelope(envelope) {
    if (!envelope || typeof envelope !== "object") {
      return;
    }
    var instance = (envelope.server && envelope.server.instance_id) || null;
    if (instance !== null && instance !== serverInstanceId) {
      if (retiredServerInstances[instance]) {
        // A late response from a viewer process this page has already left behind.
        return;
      }
      if (serverInstanceId !== null) {
        // The viewer was restarted on the same address: its states are numbered from 1 again and its snapshot ids
        // say nothing about the previous process's. Forget that ordering and any frame in flight, and stage the new
        // server's snapshot even where its id matches the one shown; the shown frame stays until the swap.
        retiredServerInstances[serverInstanceId] = true;
        latestSeenStateSeq = -1;
        stopPendingPoll();
        transitionToken++;
        stagingSnapshot = null;
        queuedSnapshot = null;
        displayedSnapshotId = null;
      }
      serverInstanceId = instance;
    }
    var seq = typeof envelope.state_seq === "number" ? envelope.state_seq : 0;
    // Strictly discard late / out-of-order responses superseded by a newer state_seq!
    if (seq < latestSeenStateSeq) {
      return;
    }
    latestSeenStateSeq = seq;
    latestStatusEnvelope = envelope;
    // Always reflect the newest input status immediately, independent of displayed/staging snapshot!
    renderBannerError(latestStatusEnvelope);

    var snap = envelope.snapshot || envelope.last_valid_snapshot;
    if (!snap) {
      stopPendingPoll();
      transitionToken++;
      stagingSnapshot = null;
      queuedSnapshot = null;
      hideUpdating();
      commitSnapshotPanel(null);
      return;
    }

    // Same snapshot already displayed: if nothing is staging, refresh metadata/banner immediately;
    // if an older snapshot is still staging, clear any stale queue and keep displayed coherent.
    if (snap.snapshot_id === displayedSnapshotId) {
      queuedSnapshot = null;
      if (stagingSnapshot === null) {
        hideUpdating();
        commitSnapshotPanel(snap);
      }
      return;
    }

    // Coalescing single-flight stager: never cancel a valid in-flight scene load!
    if (stagingSnapshot !== null) {
      if (snap.snapshot_id === stagingSnapshot.snapshot_id) {
        // Keep the newest copy of the snapshot metadata while staging continues.
        stagingSnapshot = snap;
        return;
      }
      // Coalesce newer snapshots into the single queue slot; it will start immediately when staging finishes.
      queuedSnapshot = snap;
      showUpdating(t("updatingRev", { rev: snap.revision }));
      return;
    }

    // Idle: start staging this snapshot immediately.
    startStaging(snap);
  }

  function scheduleSnapshotRetry() {
    if (snapshotRetryTimer !== null) {
      return;
    }
    var delay = Math.min(RETRY_MAX_MS, RETRY_BASE_MS * Math.pow(2, Math.max(0, snapshotFailures - 1)));
    snapshotRetryTimer = window.setTimeout(function () {
      snapshotRetryTimer = null;
      fetchSnapshot();
    }, delay);
  }

  function fetchSnapshot() {
    var mySeq = ++snapshotRequestSeq;
    return fetch("/api/snapshot", { cache: "no-store" })
      .then(function (r) {
        if (!r.ok) {
          throw new Error("HTTP " + r.status);
        }
        return r.json();
      })
      .then(function (envelope) {
        snapshotAnsweredSeq = Math.max(snapshotAnsweredSeq, mySeq);
        if (snapshotFailures > 0) {
          // The API answers again: drop the read error and the disconnected badge before applying the answer.
          snapshotFailures = 0;
          if (liveState === "disconnected") {
            liveState = typeof EventSource === "undefined" ? "polling" : (eventsOpen ? "live" : "reconnecting");
            renderLiveBadge();
          }
          renderBannerError(latestStatusEnvelope);
        }
        handleEnvelope(envelope);
      }, function (err) {
        if (mySeq < snapshotAnsweredSeq) {
          // A newer read already succeeded; this older failure says nothing about the API now.
          return;
        }
        // Nothing else may ask again (no further file change, hence no event), so keep asking on a growing pause
        // until the API answers; the verified frame and its panel stay as they are meanwhile.
        snapshotFailures += 1;
        liveState = "disconnected";
        renderLiveBadge();
        var box = el("banner-error");
        box.className = "banner banner-error";
        box.textContent = "Failed to load /api/snapshot: " + err;
        scheduleSnapshotRetry();
      });
  }

  function connectEvents() {
    if (typeof EventSource === "undefined") {
      liveState = "polling";
      renderLiveBadge();
      window.setInterval(fetchSnapshot, 1000);
      return;
    }
    var es = new EventSource("/api/events");
    es.addEventListener("open", function () {
      eventsOpen = true;
      liveState = "live";
      renderLiveBadge();
    });
    es.addEventListener("state", function () {
      liveState = "live";
      renderLiveBadge();
      fetchSnapshot();
    });
    es.addEventListener("snapshot", function () {
      liveState = "live";
      renderLiveBadge();
      fetchSnapshot();
    });
    es.addEventListener("status", function () {
      liveState = "live";
      renderLiveBadge();
      fetchSnapshot();
    });
    es.addEventListener("error", function () {
      eventsOpen = false;
      liveState = "reconnecting";
      renderLiveBadge();
    });
  }

  function setLang(next) {
    if (next !== "en" && next !== "zh") {
      return;
    }
    lang = next;
    try {
      window.sessionStorage.setItem("os_optics_viewer_lang", next);
    } catch (_e) {
      // ignore storage errors
    }
    applyStaticLabels();
    syncIframeLang(getActiveFrame(), true);
    if (displayedSnapshot) {
      commitSnapshotPanel(displayedSnapshot);
    } else if (latestStatusEnvelope) {
      renderBannerError(latestStatusEnvelope);
    }
  }

  el("btn-lang-en").addEventListener("click", function () { setLang("en"); });
  el("btn-lang-zh").addEventListener("click", function () { setLang("zh"); });

  applyStaticLabels();
  fetchSnapshot();
  connectEvents();
})();
