import type { Credibility, CredibilityTier } from "@/api/video";
import { cn } from "@/lib/utils";

export const TIER_CLASS: Record<CredibilityTier, string> = {
  trusted: "border-success text-success",
  partial: "border-border text-text-muted",
  unaudited: "border-warning text-warning",
  spoken: "border-danger text-danger",
  serious: "border-danger text-danger",
  verify: "border-warning text-warning",
};

export const TIER_LABEL: Record<CredibilityTier, string> = {
  trusted: "有出处可信",
  partial: "部分无出处",
  unaudited: "未审计",
  spoken: "口播有问题",
  serious: "有严重问题",
  verify: "有待核实",
};

/** How far a script's words can be trusted. Informs, never blocks; hover for the reasons. */
export function CredibilityBadge({ value, className }: { value: Credibility | null | undefined; className?: string }) {
  if (!value) return null;
  return (
    <span className={cn("badge border", TIER_CLASS[value.tier], className)} title={value.reasons.join("；")}>
      {value.text}
    </span>
  );
}
