-- RoadMap.md Phase 6.6/6.7: store score-coverage metadata alongside scores so
-- historical comparisons stay interpretable after scoring changes, and tighten
-- integrity constraints.

alter table audits
    add column if not exists declared_weights jsonb not null default '{}'::jsonb,
    add column if not exists measured_weight numeric(6, 4) not null default 1
        check (measured_weight between 0 and 1),
    add column if not exists unmeasured_weight numeric(6, 4) not null default 0
        check (unmeasured_weight between 0 and 1),
    add column if not exists unmeasured_dimensions jsonb not null default '[]'::jsonb;

-- Latest-audit lookups are per (domain, profile); the 0001 index only covers domain.
create index if not exists idx_audits_domain_profile_created_at
    on audits (domain, profile, created_at desc);

-- Domain sanity. NOT VALID enforces the rule for all new/updated rows without
-- failing the migration on pre-existing rows that predate it.
alter table prospects
    add constraint prospects_domain_format
    check (domain = btrim(domain) and char_length(domain) between 1 and 253 and domain !~ '\s')
    not valid;

alter table audits
    add constraint audits_domain_format
    check (domain = btrim(domain) and char_length(domain) between 1 and 253 and domain !~ '\s')
    not valid;
