--
-- PostgreSQL database dump
--

-- Dumped from database version 17.5
-- Dumped by pg_dump version 17.5

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: artifact; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.artifact (
    task_id text NOT NULL,
    name text NOT NULL,
    path text NOT NULL,
    size integer NOT NULL,
    body text
);


ALTER TABLE public.artifact OWNER TO cfbot;

--
-- Name: branch; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.branch (
    id integer NOT NULL,
    commitfest_id integer NOT NULL,
    submission_id integer NOT NULL,
    commit_id text,
    status text NOT NULL,
    url text,
    created timestamp with time zone NOT NULL,
    modified timestamp with time zone NOT NULL,
    version text,
    patch_count integer,
    first_additions integer,
    first_deletions integer,
    all_additions integer,
    all_deletions integer
);


ALTER TABLE public.branch OWNER TO cfbot;

--
-- Name: branch_id_seq; Type: SEQUENCE; Schema: public; Owner: cfbot
--

CREATE SEQUENCE public.branch_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.branch_id_seq OWNER TO cfbot;

--
-- Name: branch_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cfbot
--

ALTER SEQUENCE public.branch_id_seq OWNED BY public.branch.id;


--
-- Name: build; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.build (
    build_id text NOT NULL,
    branch_name text,
    status text,
    commit_id text,
    created timestamp with time zone NOT NULL,
    modified timestamp with time zone NOT NULL
);


ALTER TABLE public.build OWNER TO cfbot;

--
-- Name: highlight; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.highlight (
    task_id text NOT NULL,
    type text NOT NULL,
    source text NOT NULL,
    excerpt text NOT NULL
);


ALTER TABLE public.highlight OWNER TO cfbot;

--
-- Name: submission; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.submission (
    commitfest_id integer NOT NULL,
    submission_id integer NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    authors text[] NOT NULL,
    last_email_time timestamp with time zone,
    last_email_time_checked timestamp with time zone,
    last_message_id text,
    last_branch_message_id text,
    last_branch_commit_id text,
    last_branch_time timestamp with time zone,
    backoff_until timestamp with time zone,
    last_backoff interval
);


ALTER TABLE public.submission OWNER TO cfbot;

--
-- Name: task; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.task (
    commitfest_id integer NOT NULL,
    submission_id integer NOT NULL,
    task_name text NOT NULL,
    commit_id text,
    status text NOT NULL,
    created timestamp with time zone NOT NULL,
    modified timestamp with time zone NOT NULL,
    task_id text NOT NULL,
    "position" integer NOT NULL,
    build_id text
);


ALTER TABLE public.task OWNER TO cfbot;

--
-- Name: task_command; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.task_command (
    task_id text NOT NULL,
    name text NOT NULL,
    status text NOT NULL,
    type text NOT NULL,
    duration interval NOT NULL,
    log text
);


ALTER TABLE public.task_command OWNER TO cfbot;

--
-- Name: test; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.test (
    task_id text NOT NULL,
    command text NOT NULL,
    type text NOT NULL,
    suite text NOT NULL,
    name text NOT NULL,
    result text NOT NULL,
    duration interval
);


ALTER TABLE public.test OWNER TO cfbot;

--
-- Name: test_statistics; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.test_statistics (
    submission_id integer NOT NULL,
    task_name text NOT NULL,
    command text NOT NULL,
    suite text NOT NULL,
    test text NOT NULL,
    other_avg real,
    patched_avg real,
    t real,
    p real
);


ALTER TABLE public.test_statistics OWNER TO cfbot;

--
-- Name: work_queue; Type: TABLE; Schema: public; Owner: cfbot
--

CREATE TABLE public.work_queue (
    id integer NOT NULL,
    type text NOT NULL,
    key text,
    status text NOT NULL,
    retries integer,
    lease timestamp with time zone
);


ALTER TABLE public.work_queue OWNER TO cfbot;

--
-- Name: work_queue_id_seq; Type: SEQUENCE; Schema: public; Owner: cfbot
--

CREATE SEQUENCE public.work_queue_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.work_queue_id_seq OWNER TO cfbot;

--
-- Name: work_queue_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: cfbot
--

ALTER SEQUENCE public.work_queue_id_seq OWNED BY public.work_queue.id;


--
-- Name: branch id; Type: DEFAULT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.branch ALTER COLUMN id SET DEFAULT nextval('public.branch_id_seq'::regclass);


--
-- Name: work_queue id; Type: DEFAULT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.work_queue ALTER COLUMN id SET DEFAULT nextval('public.work_queue_id_seq'::regclass);


--
-- Name: artifact artifact_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.artifact
    ADD CONSTRAINT artifact_pkey PRIMARY KEY (task_id, name, path);


--
-- Name: branch branch_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.branch
    ADD CONSTRAINT branch_pkey PRIMARY KEY (id);


--
-- Name: build build_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.build
    ADD CONSTRAINT build_pkey PRIMARY KEY (build_id);


--
-- Name: submission submission_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.submission
    ADD CONSTRAINT submission_pkey PRIMARY KEY (commitfest_id, submission_id);


--
-- Name: task task_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.task
    ADD CONSTRAINT task_pkey PRIMARY KEY (task_id);


--
-- Name: test test_pkey1; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.test
    ADD CONSTRAINT test_pkey1 PRIMARY KEY (task_id, command, type, suite, name);


--
-- Name: test_statistics test_statistics_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.test_statistics
    ADD CONSTRAINT test_statistics_pkey PRIMARY KEY (submission_id, task_name, command, suite, test);


--
-- Name: work_queue work_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.work_queue
    ADD CONSTRAINT work_queue_pkey PRIMARY KEY (id);


--
-- Name: branch_submission_id_created_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX branch_submission_id_created_idx ON public.branch USING btree (submission_id, created);


--
-- Name: build_commit_id_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX build_commit_id_idx ON public.build USING btree (commit_id);


--
-- Name: highlight_task_id_type_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX highlight_task_id_type_idx ON public.highlight USING btree (task_id, type);


--
-- Name: task_command_task_id_name_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX task_command_task_id_name_idx ON public.task_command USING btree (task_id, name);


--
-- Name: task_commit_id_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX task_commit_id_idx ON public.task USING btree (commit_id);


--
-- Name: task_submission_id_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX task_submission_id_idx ON public.task USING btree (submission_id);


--
-- Name: work_queue_type_key_idx; Type: INDEX; Schema: public; Owner: cfbot
--

CREATE INDEX work_queue_type_key_idx ON public.work_queue USING btree (type, key) WHERE (status = 'NEW'::text);


--
-- Name: branch branch_commitfest_id_submission_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.branch
    ADD CONSTRAINT branch_commitfest_id_submission_id_fkey FOREIGN KEY (commitfest_id, submission_id) REFERENCES public.submission(commitfest_id, submission_id);


--
-- Name: task build_result_commitfest_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.task
    ADD CONSTRAINT build_result_commitfest_id_fkey FOREIGN KEY (commitfest_id, submission_id) REFERENCES public.submission(commitfest_id, submission_id);


--
-- Name: task task_build_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: cfbot
--

ALTER TABLE ONLY public.task
    ADD CONSTRAINT task_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.build(build_id);


--
-- PostgreSQL database dump complete
--

