// Moving between screens (decision 58). The router's history is wrapped so every change of page
// runs inside a view transition: the browser photographs the old screen, React renders the new one
// synchronously, and motion.css animates between the two. The sidebar and top bar stay still, the
// page slides the way you're going (deeper → from the right, back up → from the left, sideways →
// a quick dissolve), and the record you tapped grows into the heading of its own page.
//
// Also owns scrolling: a new page starts at the top, Back returns to where you were.
// Browsers without view transitions get the same navigation without the animation.

import { createBrowserHistory, type BrowserHistory, type Location } from "@remix-run/router";
import { flushSync } from "react-dom";

type Direction = "forward" | "back" | "same";

const scrolls = new Map<string, number>();
// The row or card someone just clicked ("deals:5"), so its title can grow into the record's page.
let clicked: { key: string; at: number } | null = null;

if (typeof document !== "undefined") {
  document.addEventListener("click", (e) => {
    const row = (e.target as Element | null)?.closest?.("[data-hero-row]") as HTMLElement | null;
    clicked = row ? { key: row.dataset.heroRow!, at: Date.now() } : null;
  }, true);
}

export function createFluidHistory(): BrowserHistory {
  const history = createBrowserHistory({ v5Compat: true });
  if ("scrollRestoration" in window.history) window.history.scrollRestoration = "manual";
  let current: Location = history.location;
  const listen = history.listen.bind(history);

  history.listen = (listener) => listen((update) => {
    const from = current;
    const to = update.location;
    current = to;
    scrolls.set(from.key, window.scrollY);
    // Same page, new filters or tab: no transition, and the page stays where it is.
    if (from.pathname === to.pathname) {
      listener(update);
      return;
    }
    const settle = () => restoreScroll(to, update.action);
    const start = (document as Document & { startViewTransition?: unknown }).startViewTransition;
    if (typeof start !== "function" || document.visibilityState !== "visible") {
      listener(update);
      settle();
      return;
    }

    const root = document.documentElement;
    const dir = direction(from.pathname, to.pathname, update.action, update.delta);
    root.dataset.nav = dir;
    const hero = heroKey(to.pathname);
    const before = hero ? findHero(hero) : null;
    const shape = before ? aspect(before) : 0;
    if (before) before.style.viewTransitionName = "df-hero";
    let after: HTMLElement | null = null;

    const t = document.startViewTransition(() => {
      if (before) before.style.viewTransitionName = "";
      flushSync(() => listener(update));
      settle();
      after = hero && before ? findHero(hero) : null;
      // Only when the two look alike (a one-line name growing into a one-line heading). A name that
      // wraps differently would stretch, so the old one just fades with its page instead.
      if (after && !similar(shape, aspect(after))) after = null;
      if (after) after.style.viewTransitionName = "df-hero";
    });
    t.ready.catch(() => {});
    t.updateCallbackDone.catch(() => {});
    t.finished.catch(() => {}).finally(() => {
      if (after) after.style.viewTransitionName = "";
      if (root.dataset.nav === dir) delete root.dataset.nav;
    });
  });
  return history;
}

/** Deeper pages slide in from the right; going back up, from the left; moving between the sidebar's
 * own pages (or sideways between two pages at the same level) is a dissolve. */
export function direction(from: string, to: string, action: string, delta: number | null): Direction {
  const a = depth(from);
  const b = depth(to);
  if (a <= 2 && b <= 2) return "same";
  if (action === "POP" && delta) return delta < 0 ? "back" : "forward";
  if (b > a) return "forward";
  if (b < a) return "back";
  return a >= 3 ? "forward" : "same";
}

function depth(path: string) {
  return path.split("/").filter(Boolean).length;
}

/** Which record's title should carry across: the one just clicked if we're opening it, otherwise
 * the record whose page we're leaving (so it settles back into its row in the list). */
function heroKey(toPath: string): string | null {
  if (clicked && Date.now() - clicked.at < 1000) {
    const [mod, id] = clicked.key.split(":");
    clicked = null;
    if (toPath === `/m/${mod}/${id}`) return `${mod}:${id}`;
    return null;
  }
  const h = document.querySelector<HTMLElement>("h1 [data-hero]");
  return h?.dataset.hero ?? null;
}

/** The first element showing that record's title that is actually on screen. */
function findHero(key: string): HTMLElement | null {
  const els = document.querySelectorAll<HTMLElement>(`[data-hero="${CSS.escape(key)}"]`);
  for (const el of els) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < window.innerHeight) return el;
  }
  return null;
}

function aspect(el: HTMLElement) {
  const r = el.getBoundingClientRect();
  return r.height ? r.width / r.height : 0;
}

function similar(a: number, b: number) {
  return a > 0 && b > 0 && b / a > 0.6 && b / a < 1.8;
}

function restoreScroll(to: Location, action: string) {
  const y = action === "POP" ? scrolls.get(to.key) ?? 0 : 0;
  window.scrollTo(0, y);
  if (y <= 0) return;
  // The page may still be growing (data from the cache arrives a frame later): keep trying briefly.
  const until = performance.now() + 400;
  const again = () => {
    if (Math.abs(window.scrollY - y) < 2 || performance.now() > until) return;
    window.scrollTo(0, y);
    requestAnimationFrame(again);
  };
  requestAnimationFrame(again);
}
