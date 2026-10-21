export type Action = "view" | "create" | "edit" | "delete";
export type ModulePerms = Record<Action, boolean>;
export type Flags = { manage_users: boolean; manage_settings: boolean; restore_archive: boolean; see_team_performance: boolean; send_campaigns: boolean };

export type Me = {
  id: number;
  email: string;
  full_name: string;
  role: { id: number; name: string; is_admin: boolean };
  perms: Record<string, ModulePerms>;
  flags: Flags;
  date_format: "DD/MM/YYYY" | "MM/DD/YYYY" | "YYYY-MM-DD";
  number_grouping: "indian" | "international";
  must_change_password: boolean;
  mfa_enrol_required: boolean;
};

export type SessionInfo = { idle_seconds: number; idle_left: number; absolute_left: number };

export type AppInfo = {
  session: SessionInfo | null;
  needs_setup: boolean;
  demo: boolean;
  sample: boolean;
  guest_available: boolean;
  is_guest: boolean;
  workspace_kind: "firm" | "individual";
  firm_name: string;
  user: Me | null;
  built_modules: string[];
  modules: { key: string; label: string }[];
  date_formats: string[];
  number_groupings: { value: string; label: string }[];
};

export type FieldType =
  | "text" | "longtext" | "url" | "email" | "phone" | "integer" | "decimal" | "percent" | "date"
  | "boolean" | "tristate" | "choice" | "dropdown" | "user" | "money" | "tags" | "link" | "birthday" | "datetime";

export type FieldSchema = {
  key: string;
  label: string;
  type: FieldType;
  group: string;
  required: boolean;
  choice_list: string | null;
  multi: boolean; // dropdown, user and link fields that take several values
  choices: { value: string; label: string }[];
  help: string;
  list_default: boolean;
  filterable: boolean;
  sortable: boolean;
  can_disable: boolean;
  enabled: boolean;
  targets: string[]; // link: modules it can point at (only built ones)
  target_labels: Record<string, string>; // link: e.g. { investors: "Investor side" }
  via: string | null; // company-level field shown on an outreach attempt: the link field holding it
  via_module: string | null;
  pipeline_role: "stage" | "status" | "entered" | "exit" | null; // exit: a reason or date asked when leaving
  readonly: boolean; // set by the app, never typed in (e.g. "From outreach")
  default_me: boolean; // user fields: a new record starts with whoever adds it
  default: string | null; // choice fields: the starting value for a new record
  min: number | null; // integer: lowest allowed (years)
  max: number | null; // integer: highest allowed
};

export type Section = {
  key: string;
  label: string;
  status: "ready" | "coming" | "hidden";
  available: boolean;
  available_with?: string; // placeholder sections: the module that will fill it
  module?: string; // related-records sections
  link_field?: string;
  via?: string; // link through this record's link field (e.g. an outreach attempt's company)
  exclude_self?: boolean;
  hide_empty?: boolean;
  params?: Record<string, string>;
  columns?: string[];
  order?: string;
  panel?: string; // a purpose-built panel instead of a table (e.g. a deal's investor tracker)
  add_label?: string; // text of the Add button (default "Add <singular>")
  no_add?: boolean; // records here are created elsewhere (e.g. a won mandate's deal)
};

export type Segments = { param: string; options: { value: string; label: string }[]; default: string } | null;

export type PipelineExit = {
  value: string;
  label: string; // the status, e.g. "Lost"
  action: string; // the button, e.g. "Mark as lost"
  done: string; // what happened, e.g. "Marked lost"
  paused: boolean; // on hold: resumes where it stopped; the others are reopened
  reason_field: string | null;
  date_field: string | null;
  tone: "red" | "orange" | "gray";
};
export type Pipeline = {
  stage_field: string;
  status_field: string;
  entered_field: string;
  open_value: string;
  won: string | null;
  stages: { value: string; label: string }[];
  groups: { label: string; stages: string[] }[]; // phases, e.g. Preparation / Execution
  exits: PipelineExit[];
};

export type Note = {
  id: number;
  kind: "note" | "call" | "meeting" | "email" | "stage";
  date: string;
  text: string;
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  meta: Record<string, any>;
  created_by: { id: number; name: string } | null;
  created_at: string;
  updated_at: string;
  can_edit: boolean;
  can_delete?: boolean;
};

/** A link to one record in another module, as the API sends it. */
export type LinkValue = { module: string; id: number; title: string; archived: boolean };
export type LookupItem = LinkValue & { subtitle: string };

export type ModuleSchema = {
  key: string;
  label: string;
  singular: string;
  title_field: string;
  groups: string[];
  fields: FieldSchema[];
  sections: Section[];
  segments: Segments;
  quick_filters: { param: string; value: string; label: string; help: string }[]; // on/off buttons above the list
  pipeline: Pipeline | null;
  timeline: boolean;
  importable: boolean;
  mergeable: boolean;
  finds_duplicates: boolean;
  perms: ModulePerms;
};

export type ChoiceOption = { id: number; label: string; is_active: boolean; order: number };
export type ChoiceList = { key: string; label: string; module: string; description: string; options: ChoiceOption[] };

export type Schema = {
  modules: ModuleSchema[];
  choice_lists: Record<string, ChoiceList>;
  currencies: { code: string; symbol: string }[];
  units: { value: string; label: string }[];
};

export type Money = { amount: string | null; currency: string; unit: string };
export type UserRef = { id: number; name: string } | null;

export type RecordData = {
  id: number;
  _title: string;
  _stamps: {
    created_at: string | null;
    created_by: UserRef;
    updated_at: string | null;
    updated_by: UserRef;
    archived_at: string | null;
    archived_by: UserRef;
  };
  [key: string]: unknown;
};

export type Page<T> = {
  count: number; next: string | null; previous: string | null; results: T[];
  restricted?: import("./deals/Restricted").Placeholder[]; // restricted deals left out (deals and tracker lists)
};

export type UserLookup = { id: number; full_name: string; is_active: boolean };
