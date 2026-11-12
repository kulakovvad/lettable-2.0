-- TraceLease schema. Plain Postgres (Supabase or local Docker).
-- Append-only: facts, cells, labels and the other journals reject UPDATE and DELETE.
-- extraction_runs is the one exception: finish_run() fills finished_at, tokens and cost.

create table if not exists documents (
    id           text primary key,
    filename     text not null,
    sha256       text not null,
    source       text not null default 'acuitus',
    page_count   integer not null,
    received_at  timestamptz not null default now()
);

create table if not exists extraction_runs (
    id              text primary key,
    extractor       text not null,
    model           text not null,
    prompt_version  text not null,
    started_at      timestamptz not null default now(),
    finished_at     timestamptz,
    tokens          integer,
    cost            numeric,
    doc_ids         text[] not null default '{}'
);

create table if not exists snapshots (
    id             text primary key,
    provider       text not null,
    query_sql      text not null,
    executed_at    timestamptz not null default now(),
    row_count      integer not null,
    result_sha256  text not null,
    row_ids        text[] not null default '{}'
);

create table if not exists facts (
    id             text primary key,
    doc_id         text not null references documents(id),
    unit_id        text,
    field          text not null,
    value          jsonb,
    unit           text,
    page           integer,
    bbox           double precision[],
    quote          text,
    grounded       boolean,
    date_check     text,
    confidence     double precision,
    review_status  text check (review_status in ('auto', 'review')),
    review_reason  text,
    run_id         text references extraction_runs(id),
    extractor      text not null,
    model_version  text,
    author         text not null,
    created_at     timestamptz not null default now(),
    supersedes_id  text references facts(id),
    reason         text,
    snapshot_id    text references snapshots(id),
    -- a fact that supersedes another one must say why
    constraint correction_has_reason check (supersedes_id is null or coalesce(reason, '') <> '')
);
create index if not exists facts_doc_idx on facts(doc_id, extractor);
create index if not exists facts_supersedes_idx on facts(supersedes_id);

create table if not exists proforma_versions (
    id               text primary key,
    doc_id           text not null references documents(id),
    version_no       integer not null,
    trigger_fact_id  text references facts(id),
    author           text not null,
    reason           text,
    created_at       timestamptz not null default now(),
    unique (doc_id, version_no)
);

create table if not exists cells (
    id              text primary key,
    version_id      text not null references proforma_versions(id),
    name            text not null,
    formula         text not null,
    input_fact_ids  text[] not null default '{}',
    input_cell_ids  text[] not null default '{}',
    value           jsonb,
    unit            text,
    author          text,
    reason          text,
    computed_at     timestamptz not null default now()
);
create index if not exists cells_version_idx on cells(version_id);

create table if not exists labels (
    id            text primary key,
    doc_id        text not null references documents(id),
    unit_id       text,
    field         text not null,
    value         jsonb,
    page          integer,
    quote         text,
    label_source  text not null check (label_source in ('parser', 'arbiter')),
    status        text not null check (status in ('agreed', 'disputed')),
    created_at    timestamptz not null default now()
);
create index if not exists labels_doc_idx on labels(doc_id);

create table if not exists eval_runs (
    id             text primary key,
    eval_run_id    text not null,
    extractor      text not null,
    run_at         timestamptz not null default now(),
    doc_id         text not null references documents(id),
    unit_id        text,
    field          text not null,
    expected       jsonb,
    got            jsonb,
    verdict        text not null check (verdict in ('caught', 'missed', 'wrong', 'invented', 'ungrounded')),
    fact_id        text references facts(id),
    label_id       text references labels(id),
    review_status  text
);
create index if not exists eval_runs_run_idx on eval_runs(eval_run_id);

create or replace function reject_mutation() returns trigger language plpgsql as $$
begin
    raise exception 'table % is append-only: % rejected', tg_table_name, tg_op;
end;
$$;

do $$
declare t text;
begin
    foreach t in array array['documents', 'facts', 'cells', 'labels', 'proforma_versions', 'snapshots', 'eval_runs']
    loop
        execute format('drop trigger if exists %I_append_only on %I', t, t);
        execute format(
            'create trigger %I_append_only before update or delete on %I '
            'for each row execute function reject_mutation()', t, t);
    end loop;
end;
$$;

-- Current value of a field = the head of its supersedes_id chain.
create or replace view current_facts as
select f.*
from facts f
where not exists (select 1 from facts g where g.supersedes_id = f.id);
