import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type { EChartsOption } from "echarts";
import {
  Activity,
  CalendarDays,
  ChartNoAxesCombined,
  CircleDollarSign,
  Clock3,
  ChevronDown,
  Filter,
  Gauge,
  HeartPulse,
  LogOut,
  Medal,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Target,
  Trophy,
  Users,
  WalletCards,
  X
} from "lucide-react";
import Chart from "./Chart";
import type { Bet, CommunityRecord, DashboardData, Filters, Match, Market, Player, Wallet } from "./types";

type ViewName = "overview" | "players" | "betting" | "community";

const COLORS = {
  paper: "#FFF3E7",
  surface: "#FFF9F2",
  ink: "#221A17",
  muted: "#765F58",
  line: "#D8C5B8",
  accent: "#A0283B",
  accentDeep: "#701B2B",
  accentSoft: "#F4DDE1",
  positive: "#496B55",
  warning: "#8A5613",
  steel: "#49616A",
  grid: "rgba(34, 26, 23, .16)"
};

const CHART_COLORS = [COLORS.accent, COLORS.steel, COLORS.positive, COLORS.warning, COLORS.muted, COLORS.accentDeep];
const SQUAD_LINE_COLORS = ["#B0223C", "#007D8A", "#3B7A57", "#B06B00", "#6C5DA3", "#C4522B", "#285F73", "#A03E70"];
const emptyFilters: Filters = { player: "", champion: "", result: "", from: "", to: "" };
const EXPORT_REFRESH_INTERVAL_MS = 5 * 60 * 1000;
const RANK_TIERS = new Set(["iron", "bronze", "silver", "gold", "platinum", "emerald", "diamond", "master", "grandmaster", "challenger"]);

type RankSummary = { tier: string; division: string; lp: number };
type ChartMarker = { name: string; coord: [number, number]; label?: { position: "top" | "bottom"; distance?: number } };

const VIEWS: Array<{ id: ViewName; label: string; chapter: string; title: string; description: string; icon: typeof Activity }> = [
  { id: "overview", label: "Overview", chapter: "01", title: "The Squad Ledger", description: "A clear record of ranked progress, current form, and the work still ahead.", icon: Gauge },
  { id: "players", label: "Players", chapter: "02", title: "Player Journeys", description: "Follow each player through rank changes, match form, and the shape of their game.", icon: Users },
  { id: "betting", label: "Betting", chapter: "03", title: "Points Markets", description: "Read the market book, wallet movement, and every settled outcome.", icon: CircleDollarSign },
  { id: "community", label: "Community", chapter: "04", title: "The Boulder Archive", description: "Keep the milestones, memories, records, and shared goals in one place.", icon: Sparkles }
];

function classNames(...values: Array<string | false | undefined>): string {
  return values.filter(Boolean).join(" ");
}

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

function lpDelta(value: string | number | null | undefined): number {
  return asNumber(value) ?? 0;
}

function signed(value: number): string {
  return (value > 0 ? "+" : "") + value.toLocaleString();
}

function compact(value: number): string {
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function dateLabel(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const date = new Date(String(value).slice(0, 10) + "T00:00:00");
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleDateString("en", { month: "short", day: "numeric", year: "numeric" });
}

function shortDate(value: string | null | undefined): string {
  if (!value) return "N/A";
  return String(value).slice(5, 10);
}

function timeLabel(value: string | null | undefined): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString("en", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function duration(value: number | null | undefined): string {
  if (value == null || value < 0) return "Unavailable";
  return String(Math.floor(value / 60)) + "m " + String(value % 60).padStart(2, "0") + "s";
}

function parseRank(value: string | null | undefined): RankSummary | null {
  const match = String(value || "").match(/^([A-Z]+)\s+([1-4])\s+[—-]\s+(\d+)\s+LP$/i);
  if (!match) return null;
  const tier = match[1].toLowerCase();
  const lp = Number(match[3]);
  return RANK_TIERS.has(tier) && Number.isFinite(lp) ? { tier, division: match[2], lp } : null;
}

function romanDivision(value: string): string {
  return ({ "1": "I", "2": "II", "3": "III", "4": "IV" } as Record<string, string>)[value] || value;
}

function rankDescription(value: string | null | undefined): string {
  const rank = parseRank(value);
  return rank ? `${rank.tier.toUpperCase()} ${romanDivision(rank.division)} — ${rank.lp.toLocaleString("en-US")} LP` : "Unavailable";
}

function recordLabel(label: string): string {
  if (label === "Best vision") return "Best vision score";
  if (label === "Highest damage") return "Highest champion damage";
  return label;
}

function recordValue(record: CommunityRecord): string {
  const value = asNumber(record.value);
  if (value == null) return "Unavailable";
  const label = record.label.toLowerCase();
  if (label.includes("cs/min")) return value.toFixed(1) + " CS/min";
  if (label.includes("kda")) return value.toFixed(2) + " KDA";
  if (label.includes("vision")) return Math.round(value).toLocaleString("en-US") + " vision score";
  if (label.includes("lp")) return signed(Math.round(value)) + " LP";
  if (label.includes("fastest") || label.includes("longest")) return duration(Math.round(value));
  if (label.includes("damage")) return Math.round(value).toLocaleString("en-US") + " damage to champions";
  return value.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

function clockLabel(value: Date | null): string {
  return value ? value.toLocaleTimeString("en", { hour: "numeric", minute: "2-digit" }) : "Checking now";
}

function toneForResult(result: string | null | undefined): string {
  return result === "WIN" ? "positive" : result === "LOSS" ? "negative" : "neutral";
}

function resultLabel(result: string | null | undefined): string {
  return result === "WIN" ? "W" : result === "LOSS" ? "L" : result === "DRAW" ? "D" : "?";
}

function lpChangeLabel(value: string | number | null | undefined): string {
  const parsed = asNumber(value);
  return parsed == null ? "Unavailable" : signed(parsed) + " LP";
}

function latestDailyPoint(points: Array<{ date: string; value: number | null }>): { date: string; value: number } | null {
  for (let index = points.length - 1; index >= 0; index -= 1) {
    const value = points[index].value;
    if (value != null && Number.isFinite(value)) return { date: points[index].date, value };
  }
  return null;
}

function latestDailyValue(points: Array<{ date: string; value: number | null }>): number | null {
  return latestDailyPoint(points)?.value ?? null;
}

function peakDailyValue(points: Array<{ date: string; value: number | null }>): number | null {
  const values = points.map((point) => point.value).filter((value): value is number => value != null && Number.isFinite(value));
  return values.length ? Math.max(...values) : null;
}

function CardlessSection({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={classNames("chronicle-section", className)}>{children}</section>;
}

function SectionHeading({ icon: Icon, eyebrow, title, action }: { icon: typeof Activity; eyebrow?: string; title: string; action?: ReactNode }) {
  return (
    <div className="section-heading">
      <div className="section-heading-copy">
        {eyebrow && <div className="section-kicker">{eyebrow}</div>}
        <h2><Icon size={18} strokeWidth={2.1} />{title}</h2>
      </div>
      {action}
    </div>
  );
}

function CollapsibleSection({ children, className = "", icon: Icon, eyebrow, title, meta }: { children: ReactNode; className?: string; icon: typeof Activity; eyebrow?: string; title: string; meta?: string }) {
  return (
    <details className={classNames("chronicle-section", "collapsible-section", className)}>
      <summary>
        <div className="collapsible-heading">
          <div className="section-heading-copy">
            {eyebrow && <div className="section-kicker">{eyebrow}</div>}
            <h2><Icon size={18} strokeWidth={2.1} />{title}</h2>
          </div>
          <div className="collapsible-action">{meta && <span className="section-meta">{meta}</span>}<ChevronDown size={18} aria-hidden="true" /></div>
        </div>
      </summary>
      <div className="collapsible-content">{children}</div>
    </details>
  );
}

function FolioMetric({ icon: Icon, label, value, detail, tone = "accent" }: { icon: typeof Activity; label: string; value: string; detail?: string; tone?: string }) {
  return (
    <div className={classNames("folio-metric", tone)}>
      <div className="folio-metric-label"><Icon size={15} />{label}</div>
      <strong>{value}</strong>
      {detail && <small>{detail}</small>}
    </div>
  );
}

function EmptyState({ label = "No data is recorded for this view.", instruction = "Change the filters or return after the next export." }: { label?: string; instruction?: string }) {
  return <div className="empty-state"><Activity size={19} /><div><strong>{label}</strong><span>{instruction}</span></div></div>;
}

function statusPill(status: string | null | undefined) {
  const normalized = (status || "unknown").toLowerCase();
  const tone = normalized === "settled" || normalized === "open" ? "positive" : normalized === "void" ? "neutral" : "warning";
  return <span className={classNames("pill", tone)}>{normalized}</span>;
}

function FilterBar({ data, filters, setFilters }: { data: DashboardData; filters: Filters; setFilters: (value: Filters) => void }) {
  const champions = useMemo(
    () => Array.from(new Set(data.players.flatMap((player) => player.matches.map((match) => match.champion).filter(Boolean) as string[]))).sort(),
    [data.players]
  );
  const hasFilters = Object.values(filters).some(Boolean);
  return (
    <div className="filter-band" aria-label="Dashboard filters">
      <div className="filter-band-heading"><Filter size={16} /><span>Filter the record</span>{hasFilters && <button className="icon-button quiet" title="Clear filters" aria-label="Clear filters" onClick={() => setFilters(emptyFilters)}><X size={16} /></button>}</div>
      <div className="filter-grid">
        <label>Player<select value={filters.player} onChange={(event) => setFilters({ ...filters, player: event.target.value })}><option value="">All players</option>{data.players.map((player) => <option key={player.riot_id} value={player.riot_id}>{player.game_name} #{player.tag_line}</option>)}</select></label>
        <label>Champion<select value={filters.champion} onChange={(event) => setFilters({ ...filters, champion: event.target.value })}><option value="">All champions</option>{champions.map((champion) => <option key={champion} value={champion}>{champion}</option>)}</select></label>
        <label>Result<select value={filters.result} onChange={(event) => setFilters({ ...filters, result: event.target.value })}><option value="">All results</option><option value="WIN">Wins</option><option value="LOSS">Losses</option><option value="DRAW">Draws</option></select></label>
        <label>From<input type="date" value={filters.from} onChange={(event) => setFilters({ ...filters, from: event.target.value })} /></label>
        <label>To<input type="date" value={filters.to} onChange={(event) => setFilters({ ...filters, to: event.target.value })} /></label>
      </div>
    </div>
  );
}

function matchesFor(player: Player, filters: Filters): Match[] {
  return player.matches.filter((match) => {
    const day = String(match.date || "");
    return (!filters.champion || match.champion === filters.champion) &&
      (!filters.result || match.result === filters.result) &&
      (!filters.from || day >= filters.from) &&
      (!filters.to || day <= filters.to);
  });
}

function visiblePlayers(data: DashboardData, filters: Filters): Player[] {
  return data.players.filter((player) => !filters.player || player.riot_id === filters.player);
}

function activityForMatches(matches: Match[]): DashboardData["activity"] {
  const byDay = new Map<string, DashboardData["activity"][number]>();
  for (const match of matches) {
    const day = String(match.date || "");
    if (!day) continue;
    const point = byDay.get(day) || { date: day, games: 0, wins: 0, losses: 0, draws: 0, lp_change: 0, active_players: 0 };
    point.games += 1;
    point.lp_change += lpDelta(match.lp_change);
    if (match.result === "WIN") point.wins += 1;
    else if (match.result === "LOSS") point.losses += 1;
    else if (match.result === "DRAW") point.draws += 1;
    byDay.set(day, point);
  }
  return Array.from(byDay.values()).sort((a, b) => a.date.localeCompare(b.date));
}

function chartBase(withZoom = true): EChartsOption {
  const base: EChartsOption = {
    backgroundColor: "transparent",
    animationDuration: 190,
    animationEasing: "cubicOut",
    textStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace", fontSize: 11 },
    grid: { left: 12, right: 24, top: 52, bottom: withZoom ? 54 : 30, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: COLORS.ink,
      borderColor: COLORS.ink,
      borderWidth: 1,
      textStyle: { color: COLORS.paper, fontFamily: "DM Mono, monospace", fontSize: 11 },
      axisPointer: { type: "cross", crossStyle: { color: COLORS.accent } },
      extraCssText: "box-shadow: 4px 4px 0 rgba(34,26,23,.18); border-radius: 2px;"
    },
    legend: { top: 6, type: "scroll", textStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace", fontSize: 11 }, pageTextStyle: { color: COLORS.muted } },
    xAxis: { axisLabel: { color: COLORS.muted, fontFamily: "DM Mono, monospace", fontSize: 10, hideOverlap: true }, axisLine: { lineStyle: { color: COLORS.line } }, axisTick: { lineStyle: { color: COLORS.line } } },
    yAxis: { axisLabel: { color: COLORS.muted, fontFamily: "DM Mono, monospace", fontSize: 10 }, axisLine: { show: false }, splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } }
  };
  if (withZoom) {
    base.dataZoom = [
      { type: "inside", start: 0, end: 100 },
      { type: "slider", height: 14, bottom: 5, borderColor: "transparent", backgroundColor: "rgba(34,26,23,.06)", fillerColor: "rgba(160,40,59,.2)", handleStyle: { color: COLORS.accent }, moveHandleStyle: { color: COLORS.accent } }
    ];
  }
  return base;
}

function Overview({ data, filters }: { data: DashboardData; filters: Filters }) {
  const players = visiblePlayers(data, filters);
  const matchEntries = players.flatMap((player) => matchesFor(player, filters).map((match) => ({ player, match })));
  const selectedMatches = matchEntries.map((entry) => entry.match);
  const wins = selectedMatches.filter((match) => match.result === "WIN").length;
  const losses = selectedMatches.filter((match) => match.result === "LOSS").length;
  const net = selectedMatches.reduce((sum, match) => sum + lpDelta(match.lp_change), 0);
  const dates = Array.from(new Set([...data.activity.map((point) => point.date), ...players.flatMap((player) => player.daily_lp.map((point) => point.date))]))
    .filter((day) => (!filters.from || day >= filters.from) && (!filters.to || day <= filters.to))
    .sort();
  const lpOption = useMemo<EChartsOption>(() => ({
    ...chartBase(),
    color: SQUAD_LINE_COLORS,
    xAxis: { type: "category", data: dates.map(shortDate) },
    yAxis: { type: "value", name: "LP", nameTextStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace" }, splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } },
    series: players.slice(0, 8).map((player, playerIndex) => {
      const points = player.daily_lp.filter((point) => (!filters.from || point.date >= filters.from) && (!filters.to || point.date <= filters.to));
      const values = new Map(points.map((point) => [point.date, point.value]));
      const current = latestDailyPoint(points);
      const peak = peakDailyValue(points);
      const showCurrentAnnotations = Boolean(filters.player) || players.length <= 3;
      const currentIndex = current ? dates.indexOf(current.date) : -1;
      const peakPoint = peak != null ? points.find((point) => point.value === peak) : undefined;
      const peakIndex = peakPoint ? dates.indexOf(peakPoint.date) : -1;
      const markers: ChartMarker[] = [];
      const samePoint = current && peakPoint && currentIndex >= 0 && currentIndex === peakIndex && current.value === peak;
      if (samePoint && showCurrentAnnotations && filters.player) {
        markers.push({ name: "Peak / current", coord: [currentIndex, current.value], label: { position: "top", distance: 10 } });
      } else {
        if (current && showCurrentAnnotations && currentIndex >= 0) markers.push({ name: "Current", coord: [currentIndex, current.value], label: { position: "bottom", distance: 10 } });
        if (filters.player && peak != null && peakPoint && peakIndex >= 0) markers.push({ name: "Peak", coord: [peakIndex, peak], label: { position: "top", distance: 10 } });
      }
      const lineColor = SQUAD_LINE_COLORS[playerIndex % SQUAD_LINE_COLORS.length];
      return {
        name: player.game_name,
        type: "line",
        smooth: false,
        connectNulls: false,
        showSymbol: false,
        lineStyle: { width: playerIndex === 0 ? 3 : 2.1 },
        endLabel: { show: showCurrentAnnotations, color: COLORS.ink, fontFamily: "DM Mono, monospace", fontSize: 10, formatter: (params: { seriesName?: string }) => params.seriesName || "" },
        markPoint: markers.length ? { symbol: "circle", symbolSize: 12, itemStyle: { color: lineColor, borderColor: COLORS.surface, borderWidth: 2 }, label: { show: true, color: COLORS.ink, backgroundColor: COLORS.surface, borderColor: COLORS.line, borderWidth: 1, padding: [3, 5], fontFamily: "DM Mono, monospace", fontSize: 9, formatter: "{b}" }, data: markers } : undefined,
        data: dates.map((day) => values.get(day) ?? null)
      };
    })
  }), [dates, filters.from, filters.player, filters.to, players]);
  const activitySource = filters.player || filters.champion || filters.result ? activityForMatches(selectedMatches) : data.activity;
  const activity = activitySource.filter((point) => (!filters.from || point.date >= filters.from) && (!filters.to || point.date <= filters.to));
  const activityOption = useMemo<EChartsOption>(() => ({
    ...chartBase(),
    color: [COLORS.positive, COLORS.accent, COLORS.warning, COLORS.steel],
    xAxis: { type: "category", data: activity.map((point) => shortDate(point.date)) },
    yAxis: [
      { type: "value", name: "Games", nameTextStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace" }, splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } },
      { type: "value", name: "Net LP", nameTextStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace" }, splitLine: { show: false } }
    ],
    series: [
      { name: "Wins", type: "bar", stack: "results", data: activity.map((point) => point.wins), barMaxWidth: 18 },
      { name: "Losses", type: "bar", stack: "results", data: activity.map((point) => point.losses), barMaxWidth: 18 },
      { name: "Draws", type: "bar", stack: "results", data: activity.map((point) => point.draws), barMaxWidth: 18 },
      { name: "Net LP", type: "line", yAxisIndex: 1, smooth: false, showSymbol: false, lineStyle: { width: 2 }, data: activity.map((point) => point.lp_change) }
    ]
  }), [activity]);
  const latestEntries = matchEntries.slice().sort((a, b) => String(b.match.date || "").localeCompare(String(a.match.date || ""))).slice(0, 10);
  const latestDate = latestEntries[0]?.match.date || data.summary.latest_match_date;
  return (
    <div className="view-grid overview-grid">
      <div className="folio-strip">
        <FolioMetric icon={Users} label="Tracked players" value={String(players.length)} detail={String(data.summary.players) + " in the export"} />
        <FolioMetric icon={Trophy} label="Games in view" value={String(selectedMatches.length)} detail={String(wins) + " wins / " + String(losses) + " losses"} tone="positive" />
        <FolioMetric icon={ChartNoAxesCombined} label="Net LP" value={signed(net) + " LP"} detail="Across the filtered record" tone={net >= 0 ? "positive" : "warning"} />
        <FolioMetric icon={HeartPulse} label="Last activity" value={dateLabel(latestDate)} detail={"Exported " + timeLabel(data.generated_at)} tone="steel" />
      </div>
      <CardlessSection className="span-8 dominant-section">
        <SectionHeading icon={ChartNoAxesCombined} eyebrow="Chapter note" title="Squad LP trajectories" action={<span className="section-meta">Gaps mean no recorded LP</span>} />
        <div className="chart-wrap chart-large">{players.length ? <Chart option={lpOption} ariaLabel="Squad LP trajectories with current values and gaps" height={390} /> : <EmptyState label="No tracked players match these filters." />}</div>
      </CardlessSection>
      <div className="overview-side span-4">
        <CardlessSection>
          <SectionHeading icon={Gauge} eyebrow="Recent form" title="Last results" />
          <div className="form-list">{latestEntries.map(({ player, match }, index) => <div className="form-row" key={String(match.date) + "-" + String(index) + "-" + player.riot_id}><span className={classNames("result-mark", toneForResult(match.result))}>{resultLabel(match.result)}</span><span className="grow"><strong>{player.game_name}</strong><small>{match.champion || "Champion unavailable"} / {dateLabel(match.date)}</small></span><b className={toneForResult(match.result)}>{lpChangeLabel(match.lp_change)}</b></div>)}{latestEntries.length === 0 && <EmptyState label="No matches match these filters." />}</div>
        </CardlessSection>
        <CardlessSection>
          <SectionHeading icon={Medal} eyebrow="Archive" title="Headline records" />
          <RecordList records={data.community.records.slice(0, 6)} />
        </CardlessSection>
      </div>
      <CardlessSection className="span-12 wide-section">
        <SectionHeading icon={Activity} eyebrow="Activity cut" title="Games and net LP" action={<span className="section-meta">Wins / losses / draws</span>} />
        <div className="chart-wrap">{activity.length ? <Chart option={activityOption} ariaLabel="Daily games and net LP" height={300} /> : <EmptyState label="No dated activity matches these filters." />}</div>
      </CardlessSection>
    </div>
  );
}

function RecordList({ records }: { records: CommunityRecord[] }) {
  if (!records.length) return <EmptyState label="No records are recorded yet." instruction="Return after the next ranked cycle." />;
  return <div className="record-list">{records.map((record, index) => <div className="record-row" key={record.label + "-" + record.player + "-" + String(index)}><div className="record-badge"><Trophy size={15} /></div><span className="grow"><strong>{recordLabel(record.label)}</strong><small>{record.player} / {record.champion} / {dateLabel(record.date)}</small></span><b>{recordValue(record)}</b></div>)}</div>;
}

function PlayerTable({ matches }: { matches: Match[] }) {
  const sorted = matches.slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))).slice(0, 60);
  if (!sorted.length) return <EmptyState label="No recent matches match this selection." />;
  return <div className="table-scroll"><table><thead><tr><th>Date</th><th>Result</th><th>Champion</th><th>Role</th><th>LP</th><th>KDA</th><th>CS/min</th><th>Duration</th></tr></thead><tbody>{sorted.map((match, index) => <tr key={String(match.date) + "-" + String(match.champion) + "-" + String(index)}><td>{dateLabel(match.date)}</td><td><span className={classNames("pill", toneForResult(match.result))}>{match.result || "Unavailable"}</span></td><td>{match.champion || "Unavailable"}</td><td>{match.position || "Unavailable"}</td><td className={toneForResult(match.result)}>{lpChangeLabel(match.lp_change)}</td><td>{match.kda == null ? "Unavailable" : match.kda.toFixed(2)}</td><td>{match.cs_per_min == null ? "Unavailable" : match.cs_per_min.toFixed(1)}</td><td>{duration(match.duration)}</td></tr>)}</tbody></table></div>;
}

function Players({ data, filters }: { data: DashboardData; filters: Filters }) {
  const players = visiblePlayers(data, filters);
  const player = players[0];
  const matches = player ? matchesFor(player, filters) : [];
  const filteredStats = useMemo(() => {
    const wins = matches.filter((match) => match.result === "WIN").length;
    const losses = matches.filter((match) => match.result === "LOSS").length;
    const decisive = wins + losses;
    const average = (field: keyof Match) => {
      const values = matches.map((match) => asNumber(match[field])).filter((value): value is number => value !== null);
      return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
    };
    return {
      games: matches.length,
      wins,
      losses,
      winRate: decisive ? wins / decisive * 100 : 0,
      netLp: matches.reduce((sum, match) => sum + lpDelta(match.lp_change), 0),
      championPool: new Set(matches.map((match) => match.champion).filter(Boolean)).size,
      championCounts: Object.fromEntries(Object.entries(matches.reduce<Record<string, number>>((counts, match) => { if (match.champion) counts[match.champion] = (counts[match.champion] || 0) + 1; return counts; }, {})).sort((a, b) => b[1] - a[1])),
      roleCounts: Object.fromEntries(Object.entries(matches.reduce<Record<string, number>>((counts, match) => { if (match.position) counts[match.position] = (counts[match.position] || 0) + 1; return counts; }, {})).sort((a, b) => b[1] - a[1])),
      avgKda: average("kda"),
      avgCsPerMin: average("cs_per_min"),
      avgDamage: average("damage"),
      avgDamageShare: average("damage_share"),
      avgKillParticipation: average("kill_participation"),
      avgVision: average("vision")
    };
  }, [matches]);
  const journeyPoints = player ? player.daily_lp.filter((point) => (!filters.from || point.date >= filters.from) && (!filters.to || point.date <= filters.to)) : [];
  const journeyOption = useMemo<EChartsOption>(() => {
    const current = latestDailyPoint(journeyPoints);
    const peak = peakDailyValue(journeyPoints);
    const currentIndex = current ? journeyPoints.findIndex((point) => point.date === current.date) : -1;
    const peakIndex = peak != null ? journeyPoints.findIndex((point) => point.value === peak) : -1;
    const markers: ChartMarker[] = [];
    const samePoint = current && peak != null && currentIndex >= 0 && currentIndex === peakIndex && current.value === peak;
    if (samePoint) {
      markers.push({ name: "Peak / current", coord: [currentIndex, current.value], label: { position: "top", distance: 10 } });
    } else {
      if (current && currentIndex >= 0) markers.push({ name: "Current", coord: [currentIndex, current.value], label: { position: "bottom", distance: 10 } });
      if (peak != null && peakIndex >= 0) markers.push({ name: "Peak", coord: [peakIndex, peak], label: { position: "top", distance: 10 } });
    }
    return {
      ...chartBase(),
      color: [COLORS.accent],
      legend: { show: false },
      xAxis: { type: "category", data: journeyPoints.map((point) => shortDate(point.date)) },
      yAxis: { type: "value", name: "LP", nameTextStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace" }, splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } },
      series: [{ name: "LP", type: "line", smooth: false, connectNulls: false, showSymbol: false, lineStyle: { width: 2.5 }, areaStyle: { color: "rgba(160,40,59,.1)" }, markPoint: markers.length ? { symbol: "circle", symbolSize: 12, itemStyle: { color: COLORS.accent, borderColor: COLORS.surface, borderWidth: 2 }, label: { show: true, color: COLORS.ink, backgroundColor: COLORS.surface, borderColor: COLORS.line, borderWidth: 1, padding: [3, 5], fontFamily: "DM Mono, monospace", fontSize: 9, formatter: "{b}" }, data: markers } : undefined, data: journeyPoints.map((point) => point.value) }]
    };
  }, [journeyPoints]);
  const formMatches = matches.slice().sort((a, b) => String(a.date || "").localeCompare(String(b.date || ""))).slice(-20);
  const championEntries = Object.entries(filteredStats.championCounts).slice(0, 10);
  const roleEntries = Object.entries(filteredStats.roleCounts);
  const championOption = useMemo<EChartsOption>(() => {
    const entries = championEntries.slice().reverse();
    return {
      ...chartBase(false),
      color: [COLORS.accent],
      legend: { show: false },
      grid: { left: 12, right: 34, top: 14, bottom: 18, containLabel: true },
      xAxis: { type: "value", splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } },
      yAxis: { type: "category", data: entries.map(([name]) => name), axisLabel: { color: COLORS.ink, fontFamily: "DM Mono, monospace", fontSize: 10 } },
      series: [{ name: "Games", type: "bar", barMaxWidth: 18, label: { show: true, position: "right", color: COLORS.ink, fontFamily: "DM Mono, monospace", fontSize: 10 }, data: entries.map(([, value]) => value) }]
    };
  }, [championEntries]);
  const roleOption = useMemo<EChartsOption>(() => {
    const total = roleEntries.reduce((sum, [, value]) => sum + value, 0);
    return {
      ...chartBase(false),
      color: CHART_COLORS,
      grid: { left: 12, right: 18, top: 46, bottom: 36, containLabel: true },
      xAxis: { type: "value", max: total || 1, show: false },
      yAxis: { type: "category", data: ["Role share"], show: false },
      legend: { top: 4, type: "scroll", textStyle: { color: COLORS.muted, fontFamily: "DM Mono, monospace", fontSize: 10 } },
      series: roleEntries.map(([name, value]) => ({ name, type: "bar", stack: "roles", barMaxWidth: 32, label: { show: value / (total || 1) > 0.12, position: "inside", color: COLORS.paper, fontFamily: "DM Mono, monospace", fontSize: 9, formatter: String(Math.round(value / (total || 1) * 100)) + "%" }, data: [value] }))
    };
  }, [roleEntries]);
  if (!player) return <CardlessSection><EmptyState label="No tracked players match this selection." instruction="Choose another player or clear the filters." /></CardlessSection>;
  const currentRank = parseRank(player.current_rank);
  const peakRank = parseRank(player.peak_rank);
  return (
    <div className="view-grid players-grid">
      <CardlessSection className="span-12 player-chapter">
        <div className="player-chapter-copy">
          <div className="section-kicker">Chapter {VIEWS[1].chapter} / Player record</div>
          <h2>{player.game_name}<span>#{player.tag_line}</span></h2>
          <p>{currentRank ? rankDescription(player.current_rank) : "Current rank unavailable"} / Peak {peakRank ? rankDescription(player.peak_rank) : "Unavailable"}</p>
        </div>
        <div className="player-rank-block">
          <div className="rank-summary">
            {currentRank ? <img className="rank-emblem" src={`/assets/ranks/emblem-${currentRank.tier}.png`} alt="" /> : <div className="rank-emblem rank-emblem-missing" aria-hidden="true">?</div>}
            <div className="rank-summary-copy">
              <span>Current LP</span>
              <strong>{currentRank ? currentRank.lp.toLocaleString("en-US") + " LP" : "Unavailable"}</strong>
              <small>{currentRank ? currentRank.tier.toUpperCase() + " " + romanDivision(currentRank.division) : "Rank unavailable"}</small>
            </div>
          </div>
          <small className="rank-peak">Peak {peakRank ? rankDescription(player.peak_rank) : "Unavailable"}</small>
        </div>
      </CardlessSection>
      <div className="folio-strip span-12">
        <FolioMetric icon={Trophy} label="Win rate" value={filteredStats.wins + filteredStats.losses ? filteredStats.winRate.toFixed(1) + "%" : "Unavailable"} detail={String(filteredStats.wins) + "W / " + String(filteredStats.losses) + "L"} tone="positive" />
        <FolioMetric icon={ChartNoAxesCombined} label="Net LP" value={signed(filteredStats.netLp) + " LP"} detail={player.stats.backfilled_matches ? String(player.stats.backfilled_matches) + " backfilled matches" : "Filtered matches"} tone={filteredStats.netLp >= 0 ? "positive" : "warning"} />
        <FolioMetric icon={Sparkles} label="Champion pool" value={String(filteredStats.championPool)} detail="In the filtered view" tone="steel" />
        <FolioMetric icon={Clock3} label="Games" value={String(filteredStats.games)} detail={"Since " + dateLabel(player.daily_lp[0]?.date)} />
      </div>
      <CardlessSection className="span-8 dominant-section">
        <SectionHeading icon={ChartNoAxesCombined} eyebrow="Rank journey" title="LP over time" action={<span className="section-meta">Null points stay open</span>} />
        <div className="chart-wrap chart-large">{journeyPoints.length ? <Chart option={journeyOption} ariaLabel="Player LP journey with current and peak annotations" height={390} /> : <EmptyState label="LP history is unavailable for this selection." />}</div>
      </CardlessSection>
      <CardlessSection className="span-4">
        <SectionHeading icon={Activity} eyebrow="Match chronicle" title="Last 20 results" />
        {formMatches.length ? <div className="form-chronicle" role="list" aria-label="Last 20 match results">{formMatches.map((match, index) => <div className={classNames("chronicle-match", toneForResult(match.result))} role="listitem" key={String(match.date) + "-" + String(index)} title={(match.result || "Unavailable") + " / " + (match.champion || "Champion unavailable") + " / " + dateLabel(match.date)}><strong>{resultLabel(match.result)}</strong><span>{shortDate(match.date)}</span></div>)}</div> : <EmptyState label="No matches match this selection." />}
      </CardlessSection>
      <CardlessSection className="span-7">
        <SectionHeading icon={Sparkles} eyebrow="Champion cut" title="Champion breakdown" />
        {championEntries.length ? <Chart option={championOption} ariaLabel="Ordered champion breakdown" height={300} /> : <EmptyState label="Champion data is unavailable." />}
      </CardlessSection>
      <CardlessSection className="span-5">
        <SectionHeading icon={Users} eyebrow="Role cut" title="Role share" />
        {roleEntries.length ? <Chart option={roleOption} ariaLabel="Proportional role breakdown" height={300} /> : <EmptyState label="Role data is unavailable." />}
      </CardlessSection>
      <CardlessSection className="span-12">
        <SectionHeading icon={Gauge} eyebrow="Performance ledger" title="Recorded metrics" />
        <div className="metric-ledger">
          <Metric label="Average KDA ratio" value={filteredStats.avgKda == null ? "Unavailable" : filteredStats.avgKda.toFixed(2) + " KDA"} />
          <Metric label="Average CS/min" value={filteredStats.avgCsPerMin == null ? "Unavailable" : filteredStats.avgCsPerMin.toFixed(1) + " CS/min"} />
          <Metric label="Champion damage / game" value={filteredStats.avgDamage == null ? "Unavailable" : Math.round(filteredStats.avgDamage).toLocaleString("en-US") + " damage to champions"} />
          <Metric label="Damage share" value={filteredStats.avgDamageShare == null ? "Unavailable" : filteredStats.avgDamageShare.toFixed(1) + "%"} />
          <Metric label="Kill participation" value={filteredStats.avgKillParticipation == null ? "Unavailable" : filteredStats.avgKillParticipation.toFixed(1) + "%"} />
          <Metric label="Vision score / game" value={filteredStats.avgVision == null ? "Unavailable" : filteredStats.avgVision.toFixed(1) + " score"} />
        </div>
      </CardlessSection>
      <CollapsibleSection className="span-12" icon={CalendarDays} eyebrow="Match record" title="Recent matches" meta={String(matches.length) + " shown"}>
        <PlayerTable matches={matches} />
      </CollapsibleSection>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="metric"><span>{label}</span><strong>{value}</strong></div>;
}

function walletsByProfit(wallets: Wallet[]) {
  return wallets.slice().sort((a, b) => b.lifetime_profit - a.lifetime_profit);
}

function Betting({ data }: { data: DashboardData }) {
  const wallets = data.betting.wallets;
  const markets = data.betting.markets;
  const bets = data.betting.bets;
  const outcomes = ["WIN", "LOSS", "VOID", "PENDING"].map((outcome) => ({ name: outcome, value: bets.filter((bet) => (bet.outcome || "PENDING") === outcome).length })).filter((item) => item.value);
  const outcomeOption = useMemo<EChartsOption>(() => {
    const labels = outcomes.map((item) => item.name);
    return {
      ...chartBase(false),
      color: [COLORS.positive, COLORS.accent, COLORS.warning, COLORS.steel],
      legend: { show: false },
      xAxis: { type: "category", data: labels },
      yAxis: { type: "value", name: "Bets", splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } },
      series: [{ name: "Outcomes", type: "bar", barMaxWidth: 42, label: { show: true, position: "top", color: COLORS.ink, fontFamily: "DM Mono, monospace" }, data: outcomes.map((item) => ({ value: item.name === "LOSS" || item.name === "VOID" ? -item.value : item.value, itemStyle: { color: item.name === "WIN" ? COLORS.positive : item.name === "LOSS" ? COLORS.accent : item.name === "VOID" ? COLORS.warning : COLORS.steel } })) }]
    };
  }, [outcomes]);
  const volumeByDay = Array.from(markets.reduce((map, market) => { const day = String(market.created_at || "").slice(0, 10); if (day) map.set(day, (map.get(day) || 0) + market.total_staked); return map; }, new Map<string, number>())).sort((a, b) => a[0].localeCompare(b[0]));
  const volumeOption = useMemo<EChartsOption>(() => ({
    ...chartBase(),
    color: [COLORS.accent],
    legend: { show: false },
    xAxis: { type: "category", data: volumeByDay.map(([day]) => shortDate(day)) },
    yAxis: { type: "value", name: "Points", splitLine: { lineStyle: { color: COLORS.grid, type: "dashed" } } },
    series: [{ name: "Market volume", type: "bar", barMaxWidth: 20, data: volumeByDay.map(([, value]) => value) }]
  }), [volumeByDay]);
  const sortedWallets = walletsByProfit(wallets);
  const totalWagered = wallets.reduce((sum, wallet) => sum + wallet.lifetime_wagered, 0);
  const totalProfit = wallets.reduce((sum, wallet) => sum + wallet.lifetime_profit, 0);
  return (
    <div className="view-grid betting-grid">
      <div className="folio-strip span-12">
        <FolioMetric icon={WalletCards} label="Wallets" value={String(wallets.length)} detail="Complete leaderboard" tone="steel" />
        <FolioMetric icon={CircleDollarSign} label="Total wagered" value={compact(totalWagered) + " pts"} detail={String(bets.length) + " individual bets"} tone="accent" />
        <FolioMetric icon={Trophy} label="Markets" value={String(markets.length)} detail={String(markets.filter((market) => ["open", "locked"].includes(market.status.toLowerCase())).length) + " active"} tone="positive" />
        <FolioMetric icon={ChartNoAxesCombined} label="Net profit" value={signed(totalProfit) + " pts"} detail="Across all wallets" tone={totalProfit >= 0 ? "positive" : "warning"} />
      </div>
      <CardlessSection className="span-8 dominant-section">
        <SectionHeading icon={ChartNoAxesCombined} eyebrow="Market timeline" title="Volume by day" action={<span className="section-meta">Total staked</span>} />
        <div className="chart-wrap chart-large">{volumeByDay.length ? <Chart option={volumeOption} ariaLabel="Market volume by day" height={390} /> : <EmptyState label="No dated markets are recorded." instruction="Open a market to start the timeline." />}</div>
      </CardlessSection>
      <CardlessSection className="span-4">
        <SectionHeading icon={Activity} eyebrow="Outcome cut" title="Wins and losses" action={<span className="section-meta">Wins rise / losses fall</span>} />
        <div className="chart-wrap">{outcomes.length ? <Chart option={outcomeOption} ariaLabel="Diverging betting outcome chart" height={300} /> : <EmptyState label="No bet outcomes are recorded." />}</div>
      </CardlessSection>
      <CardlessSection className="span-5">
        <SectionHeading icon={Trophy} eyebrow="Top three" title="Wallet leaders" />
        {sortedWallets.length ? <div className="leaderboard-ribbon">{sortedWallets.slice(0, 3).map((wallet, index) => <div className="leader-row" key={wallet.member_key}><span className="leader-rank">0{index + 1}</span><span className="grow"><strong>{wallet.display_name}</strong><small>{wallet.bets_placed} bets / {wallet.win_rate.toFixed(1)}% win rate</small></span><b className={wallet.lifetime_profit >= 0 ? "positive" : "negative"}>{signed(wallet.lifetime_profit)} pts</b></div>)}</div> : <EmptyState label="No wallets are recorded." />}
      </CardlessSection>
      <CardlessSection className="span-7">
        <SectionHeading icon={WalletCards} eyebrow="Wallet record" title="Complete leaderboard" />
        <WalletTable wallets={sortedWallets} />
      </CardlessSection>
      <CollapsibleSection className="span-12" icon={Target} eyebrow="Market record" title="Market history" meta={String(markets.length) + " recorded"}>
        <MarketTable markets={markets} />
      </CollapsibleSection>
      <CardlessSection className="span-12">
        <SectionHeading icon={CircleDollarSign} eyebrow="Bet record" title="Individual results" />
        <BetTable bets={bets} />
      </CardlessSection>
    </div>
  );
}

function WalletTable({ wallets }: { wallets: Wallet[] }) {
  if (!wallets.length) return <EmptyState label="No wallets are recorded." instruction="Place a bet to create a wallet record." />;
  return <div className="table-scroll"><table><thead><tr><th>#</th><th>Member</th><th>Balance</th><th>Profit</th><th>Wagered</th><th>Win rate</th><th>Streak</th><th>Bets</th></tr></thead><tbody>{wallets.map((wallet, index) => <tr key={wallet.member_key}><td className="rank-number">{index + 1}</td><td><strong>{wallet.display_name}</strong></td><td>{wallet.balance.toLocaleString()} pts</td><td className={wallet.lifetime_profit >= 0 ? "positive" : "negative"}>{signed(wallet.lifetime_profit)} pts</td><td>{wallet.lifetime_wagered.toLocaleString()} pts</td><td>{wallet.win_rate.toFixed(1)}%</td><td>{wallet.current_streak} <small>best {wallet.best_streak}</small></td><td>{wallet.bets_placed}</td></tr>)}</tbody></table></div>;
}

function MarketTable({ markets }: { markets: Market[] }) {
  if (!markets.length) return <EmptyState label="No markets are recorded." instruction="Open a market to create the first entry." />;
  return <div className="table-scroll"><table><thead><tr><th>Market</th><th>Subject</th><th>Status</th><th>Volume</th><th>Bets</th><th>Result</th><th>Created</th></tr></thead><tbody>{markets.map((market) => <tr key={market.market_id}><td><strong>{market.title}</strong><small className="table-sub">Market {market.market_id}</small></td><td>{market.tracked_key}</td><td>{statusPill(market.status)}</td><td>{market.total_staked.toLocaleString()} pts</td><td>{market.bet_count}</td><td>{market.result || "Pending"}</td><td>{timeLabel(market.created_at)}</td></tr>)}</tbody></table></div>;
}

function BetTable({ bets }: { bets: Bet[] }) {
  if (!bets.length) return <EmptyState label="No individual bets are recorded." instruction="Place a bet to create the first result." />;
  return <div className="table-scroll"><table><thead><tr><th>Member</th><th>Market</th><th>Side</th><th>Stake</th><th>Odds</th><th>Outcome</th><th>Placed</th></tr></thead><tbody>{bets.slice(0, 120).map((bet, index) => <tr key={bet.market_id + "-" + bet.member_key + "-" + String(bet.placed_at) + "-" + String(index)}><td>{bet.display_name}</td><td><strong>{bet.market_title}</strong><small className="table-sub">Market {bet.market_id}</small></td><td>{bet.side}</td><td>{bet.stake.toLocaleString()} pts</td><td>{bet.odds == null ? "Unavailable" : bet.odds.toFixed(2)}</td><td><span className={classNames("pill", toneForResult(bet.outcome))}>{bet.outcome || bet.status || "Pending"}</span></td><td>{timeLabel(bet.placed_at)}</td></tr>)}</tbody></table></div>;
}

type ChronicleEntry = { key: string; date?: string | null; label: string; kind: string; detail: string };

function ChronicleList({ entries }: { entries: ChronicleEntry[] }) {
  const sorted = entries.slice().sort((a, b) => String(b.date || "").localeCompare(String(a.date || ""))).slice(0, 40);
  if (!sorted.length) return <EmptyState label="No milestones or memories are recorded." instruction="Save a recap memory or complete a milestone to begin the archive." />;
  return <div className="dated-chronicle">{sorted.map((entry, index) => <div className="dated-entry" key={entry.key + "-" + String(index)}><div className={classNames("entry-rule", entry.kind)} /><div className="entry-date">{dateLabel(entry.date)}</div><div className="grow"><strong>{entry.label}</strong><small>{entry.detail || "No further detail recorded."}</small></div><span className="entry-kind">{entry.kind}</span></div>)}</div>;
}

function GoalList({ goals }: { goals: DashboardData["community"]["squad_goals"] }) {
  if (!goals.length) return <EmptyState label="No squad goals are active." instruction="Create a goal in Discord to track the next push." />;
  return <div className="goal-list">{goals.map((goal) => { const ratio = goal.target ? Math.min(1, Math.max(0, goal.progress / goal.target)) : 0; return <div className="goal-row" key={goal.week + "-" + goal.metric}><div className="goal-top"><strong>{goal.name}</strong><span>{goal.progress} / {goal.target}</span></div><div className="progress"><span style={{ width: String(ratio * 100) + "%" }} /></div><small>{goal.week} / {goal.metric}</small></div>; })}</div>;
}

function SummaryList({ items }: { items: Array<{ label: string; text: string }> }) {
  if (!items.length) return <EmptyState label="No summary notes are recorded." instruction="A weekly or monthly summary will appear here when published." />;
  return <div className="summary-list">{items.slice(0, 10).map((item) => <div className="summary-row" key={item.label}><strong>{item.label}</strong><span>{item.text || "No summary text recorded."}</span></div>)}</div>;
}

function Community({ data }: { data: DashboardData }) {
  const { community } = data;
  const entries: ChronicleEntry[] = [
    ...community.historical_events.map((event) => ({ key: "event-" + event.key, date: event.date || event.created_at, label: event.label, kind: event.kind || "event", detail: "Historical event" })),
    ...community.milestones.flatMap((group) => group.events.map((event) => ({ key: "milestone-" + group.riot_id + "-" + event.key, date: event.date || event.created_at, label: event.label, kind: "milestone", detail: group.riot_id }))),
    ...community.memories.flatMap((group) => group.items.map((item, index) => ({ key: "memory-" + group.riot_id + "-" + String(index), date: item.date || item.created_at, label: String(item.name || "Saved memory"), kind: "memory", detail: [group.riot_id, item.champion || "Champion unavailable", item.result || "Result unavailable"].join(" / ") })))
  ];
  const eventCount = entries.length;
  return (
    <div className="view-grid community-grid">
      <div className="folio-strip span-12">
        <FolioMetric icon={Medal} label="Records" value={String(community.records.length)} detail="Boulder Archive" tone="warning" />
        <FolioMetric icon={Sparkles} label="Chronicle entries" value={String(eventCount)} detail="Milestones and memories" tone="accent" />
        <FolioMetric icon={CalendarDays} label="Recaps" value={String(community.weekly_summaries.length + community.monthly_summaries.length)} detail="Weekly and monthly" tone="steel" />
        <FolioMetric icon={Target} label="Squad goals" value={String(community.squad_goals.length)} detail="Current progress" tone="positive" />
      </div>
      <CardlessSection className="span-8 dominant-section">
        <SectionHeading icon={Sparkles} eyebrow="Dated chronicle" title="Milestones and memories" action={<span className="section-meta">{String(eventCount)} entries</span>} />
        <ChronicleList entries={entries} />
      </CardlessSection>
      <CardlessSection className="span-4">
        <SectionHeading icon={Target} eyebrow="Shared work" title="Squad goals" />
        <GoalList goals={community.squad_goals} />
      </CardlessSection>
      <CardlessSection className="span-5">
        <SectionHeading icon={Medal} eyebrow="Archive" title="Records" />
        <RecordList records={community.records} />
      </CardlessSection>
      <CardlessSection className="span-7">
        <SectionHeading icon={CalendarDays} eyebrow="Chapter notes" title="Weekly summaries" />
        <SummaryList items={community.weekly_summaries.map((item) => ({ label: item.week, text: item.summary }))} />
      </CardlessSection>
      <CardlessSection className="span-12">
        <SectionHeading icon={CalendarDays} eyebrow="Long view" title="Monthly summaries" />
        <SummaryList items={community.monthly_summaries.map((item) => ({ label: item.month, text: String(item.games) + " games" + (item.skipped ? " / " + item.skipped : "") }))} />
      </CardlessSection>
    </div>
  );
}

function App() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [displayName, setDisplayName] = useState("Discord member");
  const [memberKey, setMemberKey] = useState<string | null>(null);
  const [sessionResolved, setSessionResolved] = useState(false);
  const [view, setView] = useState<ViewName>("overview");
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshError, setRefreshError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null);
  const refreshInFlight = useRef(false);
  const viewerDefaultApplied = useRef(false);

  const load = async (silent = false) => {
    if (refreshInFlight.current) return;
    refreshInFlight.current = true;
    if (!silent) setLoading(true); else setRefreshing(true);
    if (!silent) setError(""); else setRefreshError("");
    try {
      const [sessionResponse, dataResponse] = await Promise.all([
        fetch("/api/session", { credentials: "same-origin" }),
        fetch("/api/dashboard", { credentials: "same-origin", cache: "no-store" })
      ]);
      if (sessionResponse.status === 401 || dataResponse.status === 401) throw new Error("Your session expired. Sign in again.");
      if (!dataResponse.ok) throw new Error("Dashboard data is temporarily unavailable.");
      const session = await sessionResponse.json();
      const payload = await dataResponse.json() as DashboardData;
      setDisplayName(session.display_name || "Discord member");
      setMemberKey(typeof session.member_key === "string" ? session.member_key : null);
      setSessionResolved(true);
      setData(payload);
      setRefreshError("");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Dashboard data is temporarily unavailable.";
      if (silent) setRefreshError("Refresh unavailable; showing the last export."); else setError(message);
    } finally {
      refreshInFlight.current = false;
      setLastCheckedAt(new Date());
      setLoading(false);
      setRefreshing(false);
    }
  };

  const logout = async () => {
    try {
      await fetch("/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      window.location.assign("/");
    }
  };

  useEffect(() => {
    void load();
    const interval = window.setInterval(() => { void load(true); }, EXPORT_REFRESH_INTERVAL_MS);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    if (view !== "players" || !data || !sessionResolved || viewerDefaultApplied.current) return;
    viewerDefaultApplied.current = true;
    if (!memberKey) return;
    const viewer = data.players.find((player) => player.member_key === memberKey);
    if (viewer && !filters.player) setFilters({ ...filters, player: viewer.riot_id });
  }, [data, filters, memberKey, sessionResolved, view]);

  if (loading) return <div className="loading-screen"><div className="loading-mark"><img src="/assets/Sisyphus-Favicon.png" alt="Sisyphus logo" /></div><p>Opening the Chronicle</p><span>Loading the latest private export.</span></div>;
  if (!data || error) return <div className="loading-screen"><div className="loading-mark error"><img src="/assets/Sisyphus-Favicon.png" alt="Sisyphus logo" /></div><h1>Chronicle unavailable</h1><p>{error || "No export is available yet."}</p><div className="error-actions"><button className="primary-button" onClick={() => void load()}><RefreshCw size={16} />Try again</button><button className="secondary-button" onClick={() => void logout()}><LogOut size={16} />Sign out</button></div></div>;

  const activeMeta = VIEWS.find((item) => item.id === view) || VIEWS[0];
  const ActiveIcon = activeMeta.icon;
  const nav = (mobile = false) => <nav className={mobile ? "mobile-nav" : "rail-nav"} aria-label="Dashboard chapters">{VIEWS.map(({ id, label, chapter, icon: Icon }) => <button key={id} className={classNames("nav-item", view === id && "active")} aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}><span className="nav-chapter">{chapter}</span><Icon size={17} /><span>{label}</span></button>)}</nav>;
  return (
    <div className="app-shell">
      <aside className="rail">
        <div className="rail-brand"><div className="brand-mark"><img src="/assets/Sisyphus-Favicon.png" alt="" /></div><div><strong>The Boulder Chronicle</strong><span>Sisyphus / v{data.source_version}</span></div></div>
        <div className="rail-label">Chapters</div>
        {nav()}
        <div className="rail-spacer" />
        <div className="rail-cut"><span className="status-dot" /><div><strong>Live record</strong><small>Exported {timeLabel(data.generated_at)}</small></div></div>
        <div className="rail-release"><span>Current release</span><strong>v{data.source_version}</strong><small>Private guild analytics</small></div>
        <div className="rail-account"><span>Signed in as</span><strong>{displayName}</strong><button type="button" onClick={() => void logout()} title="Sign out"><LogOut size={15} />Sign out</button></div>
      </aside>
      <div className="workspace">
        <header className="mobile-topbar"><div className="brand-inline"><div className="brand-mark"><img src="/assets/Sisyphus-Favicon.png" alt="" /></div><div><strong>The Boulder Chronicle</strong><span>v{data.source_version}</span></div></div><div className="topbar-actions"><span className="status-line"><span className="status-dot" />Auto 5m</span><button className="icon-button" title="Refresh dashboard" aria-label="Refresh dashboard" onClick={() => void load(true)} disabled={refreshing}><RefreshCw size={17} className={refreshing ? "spin" : ""} /></button><button className="icon-button" type="button" onClick={() => void logout()} title="Sign out" aria-label="Sign out"><LogOut size={17} /></button></div></header>
        {nav(true)}
        <main className="content">
          <div className="page-intro"><div><div className="section-kicker">Chapter {activeMeta.chapter} / Sisyphus server record</div><h1>{activeMeta.title}</h1><p>{activeMeta.description}</p></div><div className="refresh-block"><div><Clock3 size={15} />Auto-refreshes every five minutes</div><small className={classNames("refresh-checked", refreshError && "refresh-error")}>{refreshError || "Last checked " + clockLabel(lastCheckedAt)}</small><button className="refresh-text-button" onClick={() => void load(true)} disabled={refreshing}><RefreshCw size={14} className={refreshing ? "spin" : ""} />{refreshing ? "Refreshing" : "Refresh record"}</button></div></div>
          {(view === "overview" || view === "players") && <FilterBar data={data} filters={filters} setFilters={setFilters} />}
          <div className={classNames("view", "view-" + view)}><div className="view-marker"><ActiveIcon size={15} />{activeMeta.label}</div>{view === "overview" && <Overview data={data} filters={filters} />}{view === "players" && <Players data={data} filters={filters} />}{view === "betting" && <Betting data={data} />}{view === "community" && <Community data={data} />}</div>
        </main>
        <footer className="footer"><span><ShieldCheck size={14} />Discord guild access</span><span>Private export / no raw identifiers</span></footer>
      </div>
    </div>
  );
}

export default App;
