// Dealflow's look: a calm, document-like workspace. Warm neutrals, quiet borders, one blue for
// actions, small radii, soft layered shadows. Things that open arrive on springs (the curves are CSS
// variables from motion.css) and leave quickly.

import {
  ActionIcon, Badge, Button, Checkbox, Combobox, createTheme, Drawer, Input, Menu, Modal, NavLink, Paper, Popover, SegmentedControl,
  Select, Switch, Tabs, Tooltip, type CSSVariablesResolver, type MantineColorsTuple,
} from "@mantine/core";

// Warm grey: page tints, borders, secondary and primary text (9 is the ink colour).
const ink: MantineColorsTuple = [
  "#fbfbfa", "#f7f7f5", "#f1f1ef", "#e9e9e7", "#dcdcd9", "#c3c2bf", "#9b9a97", "#787774", "#55534e", "#37352f",
];
// The action colour. Kept under the old name so every existing colour reference still works.
const navy: MantineColorsTuple = [
  "#eef5fd", "#dcebfb", "#b6d4f6", "#8cbbf1", "#64a3ec", "#4a93e9", "#2f80e0", "#1f6fd0", "#1a62b8", "#123f75",
];

// Dialogs rise a little and settle; they leave faster than they came.
const sheet = {
  transition: {
    in: { opacity: 1, transform: "none" },
    out: { opacity: 0, transform: "translateY(12px) scale(.97)" },
    transitionProperty: "opacity, transform",
  },
  duration: 460, exitDuration: 170, timingFunction: "var(--df-spring-smooth)",
};
// Menus and dropdowns grow out of the control that opened them.
const pop = {
  transition: {
    in: { opacity: 1, transform: "none" },
    out: { opacity: 0, transform: "scale(.96) translateY(-4px)" },
    common: { transformOrigin: "top center" },
    transitionProperty: "opacity, transform",
  },
  duration: 340, exitDuration: 120, timingFunction: "var(--df-spring-snappy)",
};

export const theme = createTheme({
  primaryColor: "navy",
  primaryShade: 6,
  colors: { navy, gray: ink, dark: [ink[0], ink[1], ink[2], ink[4], ink[5], ink[6], ink[7], ink[8], ink[9], "#191917"] },
  black: ink[9],
  fontFamily:
    'ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI", Helvetica, "Apple Color Emoji", Arial, sans-serif',
  fontFamilyMonospace: '"SFMono-Regular", Menlo, Consolas, "Liberation Mono", monospace',
  fontSizes: { xs: "12px", sm: "14px", md: "15px", lg: "16px", xl: "18px" },
  lineHeights: { xs: "1.4", sm: "1.5", md: "1.5", lg: "1.5", xl: "1.45" },
  radius: { xs: "3px", sm: "4px", md: "6px", lg: "8px", xl: "12px" },
  defaultRadius: "sm",
  cursorType: "pointer",
  shadows: {
    xs: "0 1px 2px rgba(15,15,15,.06)",
    sm: "0 0 0 1px rgba(15,15,15,.05), 0 2px 4px rgba(15,15,15,.08)",
    md: "0 0 0 1px rgba(15,15,15,.05), 0 3px 6px rgba(15,15,15,.1), 0 9px 24px rgba(15,15,15,.12)",
    lg: "0 0 0 1px rgba(15,15,15,.05), 0 5px 10px rgba(15,15,15,.1), 0 15px 40px rgba(15,15,15,.16)",
    xl: "0 0 0 1px rgba(15,15,15,.05), 0 8px 16px rgba(15,15,15,.12), 0 24px 64px rgba(15,15,15,.2)",
  },
  headings: {
    fontWeight: "700",
    sizes: {
      h1: { fontSize: "40px", lineHeight: "1.2" },
      h2: { fontSize: "30px", lineHeight: "1.25" },
      h3: { fontSize: "22px", lineHeight: "1.3" },
      h4: { fontSize: "18px", lineHeight: "1.35" },
      h5: { fontSize: "15px", lineHeight: "1.4" },
    },
  },
  components: {
    Button: Button.extend({ defaultProps: { radius: "sm", fw: 500 } }),
    ActionIcon: ActionIcon.extend({ defaultProps: { radius: "sm" } }),
    Badge: Badge.extend({ defaultProps: { radius: "sm", tt: "none", fw: 500, variant: "light" } }),
    Paper: Paper.extend({ defaultProps: { radius: "md" } }),
    Modal: Modal.extend({
      defaultProps: {
        radius: "lg", shadow: "xl", transitionProps: sheet,
        overlayProps: { backgroundOpacity: 0.32, color: "#0f0f0f" },
      },
    }),
    Drawer: Drawer.extend({
      defaultProps: { shadow: "xl", transitionProps: { duration: 480, exitDuration: 220, timingFunction: "var(--df-spring-smooth)" },
        overlayProps: { backgroundOpacity: 0.18, color: "#0f0f0f" } },
    }),
    Menu: Menu.extend({ defaultProps: { shadow: "md", radius: "md", transitionProps: pop, offset: 6 } }),
    Popover: Popover.extend({ defaultProps: { shadow: "md", radius: "md", transitionProps: pop, offset: 6 } }),
    Tooltip: Tooltip.extend({
      defaultProps: { openDelay: 350, radius: "sm", fz: "xs", color: "dark", withArrow: false, transitionProps: { transition: "fade", duration: 140 } },
    }),
    // Every dropdown input (multi-selects, tags, dates) opens the same way.
    Combobox: Combobox.extend({ defaultProps: { shadow: "md", transitionProps: pop } }),
    Select: Select.extend({ defaultProps: { comboboxProps: { shadow: "md", transitionProps: pop } } }),
    Input: Input.extend({ defaultProps: { radius: "sm" } }),
    Checkbox: Checkbox.extend({ defaultProps: { radius: "xs" } }),
    Switch: Switch.extend({ defaultProps: { size: "sm" } }),
    SegmentedControl: SegmentedControl.extend({
      defaultProps: { radius: "sm", size: "sm", transitionDuration: 400, transitionTimingFunction: "var(--df-spring-snappy)" },
    }),
    Tabs: Tabs.extend({ defaultProps: { color: "dark" } }),
    NavLink: NavLink.extend({ defaultProps: { variant: "subtle" } }),
  },
});

export const cssVariables: CSSVariablesResolver = () => ({
  variables: {
    // Colour and shadow changes on hover (movement uses the springs in motion.css).
    "--df-ease": "cubic-bezier(.2,.8,.2,1)",
    "--df-fast": "120ms",
    "--df-medium": "180ms",
    "--df-hover": "rgba(55,53,47,.06)",
    "--df-press": "rgba(55,53,47,.1)",
    "--df-line": ink[3],
    "--df-sidebar": ink[1],
  },
  light: {
    "--mantine-color-body": "#ffffff",
    "--mantine-color-text": ink[9],
    "--mantine-color-dimmed": ink[7],
    "--mantine-color-default-border": ink[3],
    "--mantine-color-placeholder": ink[6],
    "--mantine-color-anchor": ink[9],
  },
  dark: {},
});
