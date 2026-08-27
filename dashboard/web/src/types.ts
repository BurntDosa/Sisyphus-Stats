export type Result = "WIN" | "LOSS" | "DRAW";

export interface Match {
  date?: string | null;
  recorded_at?: string | null;
  champion?: string | null;
  champion_id?: number | null;
  position?: string | null;
  result?: Result | string | null;
  lp_change?: string | number | null;
  lp_before?: number | null;
  lp_total?: number | null;
  duration?: number | null;
  kills?: number | null;
  deaths?: number | null;
  assists?: number | null;
  kda?: number | null;
  cs?: number | null;
  cs_per_min?: number | null;
  damage?: number | null;
  damage_share?: number | null;
  gold?: number | null;
  gold_share?: number | null;
  kill_participation?: number | null;
  team_kills?: number | null;
  enemy_kills?: number | null;
  champion_mastery?: number | null;
  vision?: number | null;
  wardsKilled?: number | null;
  wardsPlaced?: number | null;
  controlWardsBought?: number | null;
  backfilled?: boolean;
}

export interface Player {
  riot_id: string;
  game_name: string;
  tag_line: string;
  member_key?: string | null;
  current_lp?: number | null;
  current_rank?: string | null;
  peak_lp?: number | null;
  peak_rank?: string | null;
  last_match_date?: string | null;
  history_backfilled?: boolean;
  stats: {
    games: number;
    wins: number;
    losses: number;
    draws: number;
    win_rate: number;
    net_lp: number;
    avg_kda?: number | null;
    avg_cs_per_min?: number | null;
    avg_damage?: number | null;
    avg_damage_share?: number | null;
    avg_gold?: number | null;
    avg_kill_participation?: number | null;
    avg_vision?: number | null;
    champion_pool: number;
    role_counts: Record<string, number>;
    champion_counts: Record<string, number>;
    backfilled_matches: number;
  };
  daily_lp: Array<{ date: string; value: number | null }>;
  matches: Match[];
}

export interface ActivityPoint {
  date: string;
  games: number;
  wins: number;
  losses: number;
  draws: number;
  lp_change: number;
  active_players: number;
}

export interface Wallet {
  member_key: string;
  display_name: string;
  balance: number;
  reserved: number;
  lifetime_profit: number;
  lifetime_wagered: number;
  bets_placed: number;
  wins: number;
  losses: number;
  voids: number;
  win_rate: number;
  current_streak: number;
  best_streak: number;
}

export interface Market {
  market_id: string;
  tracked_key: string;
  title: string;
  status: string;
  created_at?: string | null;
  lock_at?: string | null;
  timeout_at?: string | null;
  resolved_at?: string | null;
  win_prob?: number | null;
  win_odds?: number | null;
  lose_odds?: number | null;
  total_staked: number;
  result?: string | null;
  winner_side?: string | null;
  winner_count: number;
  winner_stake: number;
  bet_count: number;
  outcomes: Record<string, number>;
}

export interface Bet {
  market_id: string;
  market_title: string;
  member_key: string;
  display_name: string;
  side: string;
  stake: number;
  odds?: number | null;
  use_insurance: boolean;
  status: string;
  result?: string | null;
  outcome?: string | null;
  placed_at?: string | null;
  settled_at?: string | null;
}

export interface CommunityRecord {
  label: string;
  value?: number | null;
  player: string;
  champion: string;
  date?: string | null;
}

export interface MemoryItem {
  name?: string | null;
  date?: string | null;
  champion?: string | null;
  role?: string | null;
  result?: string | null;
  lp_change?: string | number | null;
  lp_before?: number | null;
  lp_total?: number | null;
  kills?: number | null;
  deaths?: number | null;
  assists?: number | null;
  duration?: number | null;
  reason?: string | null;
  created_at?: string | null;
}

export interface CommunityData {
  records: CommunityRecord[];
  milestones: Array<{ riot_id: string; events: Array<{ key: string; label: string; date?: string | null; created_at?: string | null }> }>;
  memories: Array<{ riot_id: string; items: MemoryItem[] }>;
  weekly_summaries: Array<{ week: string; summary: string }>;
  monthly_summaries: Array<{ month: string; public_posted_at?: string | null; games: number; skipped?: string | null }>;
  squad_goals: Array<{ name: string; metric: string; target: number; progress: number; week: string; created_at?: string | null }>;
  historical_events: Array<{ key: string; date?: string | null; label: string; kind: string; created_at?: string | null }>;
}

export interface DashboardData {
  schema_version: number;
  generated_at: string;
  source_version: string;
  summary: {
    players: number;
    games: number;
    wins: number;
    losses: number;
    draws: number;
    net_lp: number;
    latest_match_date?: string | null;
    active_markets: number;
    markets: number;
    bets: number;
  };
  players: Player[];
  activity: ActivityPoint[];
  betting: { wallets: Wallet[]; markets: Market[]; bets: Bet[] };
  community: CommunityData;
}

export interface Filters {
  player: string;
  champion: string;
  result: string;
  from: string;
  to: string;
}
