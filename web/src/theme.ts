import {
  Card,
  type CSSVariablesResolver,
  createTheme,
  type MantineColorsTuple,
  Modal,
  Paper,
  Popover,
  Menu,
} from "@mantine/core";

const brand: MantineColorsTuple = [
  "#eaf2ff",
  "#d3e3ff",
  "#a6c6ff",
  "#76a7fd",
  "#4f8dfa",
  "#3b82f6",
  "#2f74ea",
  "#2563eb",
  "#1d4fc4",
  "#173f9e",
];

// 深色由 dark[7] 做底、dark[6] 做输入框底、dark[4] 做边框、dark[2] 做次要文字。
const dark: MantineColorsTuple = [
  "#eef2f7",
  "#c9d2df",
  "#9aa6b8",
  "#647185",
  "#2a3446",
  "#1e283a",
  "#18202e",
  "#0a0d13",
  "#070a0f",
  "#05070b",
];

export const theme = createTheme({
  primaryColor: "brand",
  primaryShade: { light: 7, dark: 5 },
  colors: { brand, dark },
  fontFamily: '"IBM Plex Sans", "Noto Sans SC", "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif',
  fontFamilyMonospace: '"JetBrains Mono", "Cascadia Code", Consolas, monospace',
  defaultRadius: "md",
  headings: {
    fontFamily: '"IBM Plex Sans", "Noto Sans SC", "Microsoft YaHei UI", system-ui, sans-serif',
    fontWeight: "700",
  },
  components: {
    Paper: Paper.extend({ defaultProps: { bg: "var(--mo-surface)" } }),
    Card: Card.extend({ defaultProps: { bg: "var(--mo-surface)" } }),
    Modal: Modal.extend({
      styles: { content: { background: "var(--mo-surface)" }, header: { background: "var(--mo-surface)" } },
    }),
    Popover: Popover.extend({ styles: { dropdown: { background: "var(--mo-surface-2)" } } }),
    Menu: Menu.extend({ styles: { dropdown: { background: "var(--mo-surface-2)" } } }),
  },
});

// 页面自己的配色变量，组件和页面样式只用这些，不写死颜色。界面只有深色。
export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: {},
  dark: {
    "--mo-bg": "#0a0d13",
    "--mo-side": "#0e121a",
    "--mo-surface": "#131924",
    "--mo-surface-2": "#18202e",
    "--mo-surface-3": "#1e283a",
    "--mo-line": "rgba(148, 163, 184, 0.10)",
    "--mo-line-2": "rgba(148, 163, 184, 0.18)",
    "--mo-text": "#eef2f7",
    "--mo-dim": "#9aa6b8",
    "--mo-muted": "#647185",
    "--mo-accent": "#3b82f6",
    "--mo-accent-hi": "#60a5fa",
    "--mo-accent-soft": "rgba(59, 130, 246, 0.14)",
    "--mo-accent-bd": "rgba(59, 130, 246, 0.34)",
    "--mo-source": "#38bdf8",
    "--mo-source-soft": "rgba(14, 165, 233, 0.14)",
    "--mo-source-bd": "rgba(56, 189, 248, 0.28)",
    "--mo-ok": "#34d399",
    "--mo-ok-soft": "rgba(16, 185, 129, 0.14)",
    "--mo-ok-bd": "rgba(16, 185, 129, 0.30)",
    "--mo-warn": "#fbbf24",
    "--mo-warn-soft": "rgba(245, 158, 11, 0.14)",
    "--mo-warn-bd": "rgba(245, 158, 11, 0.32)",
    "--mo-bad": "#fb7185",
    "--mo-bad-soft": "rgba(244, 63, 94, 0.14)",
    "--mo-bad-bd": "rgba(244, 63, 94, 0.32)",
    "--mo-shadow": "0 14px 34px -18px rgba(0, 0, 0, 0.85)",
    "--mantine-color-body": "#0a0d13",
  },
});
