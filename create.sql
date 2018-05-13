create table submission (
  commitfest_id int not null,
  submission_id int not null,
  name text not null,
  status text not null,
  authors text[] not null,
  last_email_time timestamptz,
  last_email_time_checked timestamptz,
  last_message_id text,
  last_branch_message_id text,
  last_branch_commit_id text,
  last_branch_time timestamptz,
  primary key (commitfest_id, submission_id)
);

create table build_result (
  id serial primary key,
  commitfest_id int not null,
  submission_id int not null,
  provider text not null, -- 'apply', 'travis', 'appveyor', ...
  message_id text not null,
  master_commit_id text not null,
  ci_commit_id text,
  result text check (result in ('success', 'failure')),
  message text,
  url text, -- build link on CI provider
  created timestamptz not null,
  modified timestamptz not null,
  published timestamptz, -- when did we send it to the commitfest app?
  foreign key (commitfest_id, submission_id) references submission (commitfest_id, submission_id)
);
