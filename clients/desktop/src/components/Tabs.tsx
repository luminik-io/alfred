import type { CSSProperties } from "react";
import type { LucideIcon } from "lucide-react";

import { Badge, TabsList, TabsRoot, TabsTrigger } from "./ui";

export type TabItem<K extends string = string> = {
  key: K;
  label: string;
  icon?: LucideIcon;
  /** Small count shown as a pill on the tab (e.g. unread activity). */
  badge?: number | null;
};

/**
 * In-page sub-navigation: a segmented control that keeps a single page's
 * large sections behind tabs instead of one long scroll. Pages own the active
 * key so the choice can be lifted (e.g. a deep-link from another surface).
 *
 * Backed by Radix Tabs for selection state, arrow-key navigation, and tab
 * semantics. Callers render their own panels as siblings (a page owns its large
 * sections), so no `TabsContent` lives inside the root.
 *
 * We pin an explicit roving `tabindex` on the triggers (active tab 0, the rest
 * -1) rather than leaving it entirely to Radix's RovingFocusGroup. Radix keeps
 * the triggers at `tabindex="-1"` and instead puts the tab stop on the tablist
 * container, redirecting focus onto the active trigger on the container's focus
 * event. That works, but the tab stop then lives on the container, not the tab,
 * so which element ends up focused depends on that redirect firing. Placing the
 * roving `tabindex="0"` directly on the active trigger makes it the tab stop
 * itself: Tab lands squarely on the active tab and programmatic/AT focus is
 * deterministic, with no behavioural change to Radix's own arrow/Home/End keys
 * (verified against the real sibling-panel wiring in a browser). This is a
 * defensive, explicit WAI-ARIA roving-tabindex, not a fix for broken arrows.
 */
export function Tabs<K extends string>({
  tabs,
  active,
  onChange,
  idBase,
  ariaLabel,
}: {
  tabs: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  idBase: string;
  ariaLabel: string;
}) {
  return (
    <TabsRoot value={active} onValueChange={(value) => onChange(value as K)}>
      <TabsList
        className="alfred-tabs-list grid w-full sm:w-fit"
        style={{ "--tab-count": tabs.length } as CSSProperties}
        aria-label={ariaLabel}
      >
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <TabsTrigger
              key={tab.key}
              id={`${idBase}-tab-${tab.key}`}
              className="min-w-0 gap-1 px-1.5 text-[0.78rem] sm:gap-1.5 sm:px-3 sm:text-sm [&>span]:min-w-0 [&>span]:truncate"
              value={tab.key}
              aria-controls={`${idBase}-panel`}
              // Explicit roving tabindex: the active tab is the tab stop, so Tab
              // lands directly on it. Radix still owns arrow/Home/End behaviour.
              tabIndex={tab.key === active ? 0 : -1}
            >
              {Icon ? (
                <Icon
                  size={15}
                  className="max-[420px]:hidden"
                  aria-hidden="true"
                />
              ) : null}
              <span>{tab.label}</span>
              {tab.badge ? (
                <Badge
                  variant="secondary"
                  className="h-4 min-w-4 px-1 text-[10px]"
                  aria-label={`${tab.badge} new`}
                >
                  {tab.badge > 9 ? "9+" : tab.badge}
                </Badge>
              ) : null}
            </TabsTrigger>
          );
        })}
      </TabsList>
    </TabsRoot>
  );
}
