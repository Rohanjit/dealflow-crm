export type Action = "view" | "create" | "edit" | "delete";
export type ModulePerms = Record<Action, boolean>;
export type Flags = { manage_users: boolean; manage_settings: boolean; restore_archive: boolean };

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
};

export type AppInfo = {
  needs_setup: boolean;
  firm_name: string;
  user: Me | null;
  built_modules: string[];
  modules: { key: string; label: string }[];
  date_formats: string[];
  number_groupings: { value: string; label: string }[];
};

export type FieldType =
  | "text" | "longtext" | "url" | "email" | "phone" | "integer" | "decimal" | "percent" | "date"
  | "boolean" | "tristate" | "choice" | "dropdown" | "user" | "money" | "tags";

export type FieldSchema = {
  key: string;
  label: string;
  type: FieldType;
  group: string;
  required: boolean;
  choice_list: string | null;
  multi: boolean;
  choices: { value: string; label: string }[];
  help: string;
  list_default: boolean;
  filterable: boolean;
  sortable: boolean;
  can_disable: boolean;
  enabled: boolean;
};

export type ModuleSchema = {
  key: string;
  label: string;
  singular: string;
  title_field: string;
  groups: string[];
  fields: FieldSchema[];
  sections: { key: string; label: string; available_with: string; available: boolean }[];
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

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };

export type UserLookup = { id: number; full_name: string; is_active: boolean };
