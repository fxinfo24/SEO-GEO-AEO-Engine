-- SEO/GEO/AEO engine — initial schema
-- Run against your Supabase project (SQL editor or `supabase db push`).

create table if not exists prospects (
    id uuid primary key default gen_random_uuid(),
    domain text not null unique,
    company text,
    contact_email text,
    status text not null default 'lead'
        check (status in ('lead', 'qualified', 'proposal', 'won', 'lost')),
    monthly_value numeric(10, 2),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists audits (
    id uuid primary key default gen_random_uuid(),
    prospect_id uuid references prospects(id) on delete set null,
    domain text not null,
    profile text not null check (profile in ('seo', 'geo', 'aeo')),
    overall_score numeric(5, 1) not null check (overall_score between 0 and 100),
    rating text not null,
    dimension_scores jsonb not null default '{}'::jsonb,
    weights jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_audits_domain_created_at on audits (domain, created_at desc);

create table if not exists audit_findings (
    id uuid primary key default gen_random_uuid(),
    audit_id uuid not null references audits(id) on delete cascade,
    severity text not null check (severity in ('critical', 'high', 'medium', 'low')),
    title text not null,
    detail text not null,
    page_url text,
    created_at timestamptz not null default now()
);

create index if not exists idx_audit_findings_audit_id on audit_findings (audit_id);
create index if not exists idx_audit_findings_severity on audit_findings (severity);

-- Keep prospects.updated_at current on any write.
create or replace function set_updated_at()
returns trigger as $$
begin
    new.updated_at = now();
    return new;
end;
$$ language plpgsql;

drop trigger if exists trg_prospects_updated_at on prospects;
create trigger trg_prospects_updated_at
    before update on prospects
    for each row execute function set_updated_at();

-- Row Level Security: service role (used by the engine's backend) bypasses RLS
-- by default in Supabase, so these policies matter once you add a client-facing
-- role. Enable and adjust before exposing this to anything but the service role.
alter table prospects enable row level security;
alter table audits enable row level security;
alter table audit_findings enable row level security;
