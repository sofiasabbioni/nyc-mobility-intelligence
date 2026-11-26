# Benchmark results: how to read them responsibly

## Scope

This guide explains how to read the project's benchmark results (PostgreSQL 16 vs DuckDB 2.0) without claiming more than they show. It adds no new measurements and changes no data, code or CSV files. Every statement comes from a file already on `main`:

- `README.md`: main results and the Mac reproduction
- `docs/METHODOLOGY.md`: how the benchmark was run and what it can support (sections 1, 5, 8 and 9)
- `docs/RESULTS.md`: the extra Q1–Q3 study (section 5) and the limits (section 6)
- `docs/BENCHMARK_RIGOR.md`: design and limits of the extra study
- `benchmark/rigor_study.py` and `tests/test_benchmark_rigor.py`: the extra-study tool and its tests
- `docs/LOADER_EVALUATION.md`: the separate Parquet-reading test
- `docs/MACHINE.md`: the computer and settings used for the main runs
- `.env.example`: default benchmark settings

## Experiments represented

| Experiment | Date | Computer | What it measured | Where reported |
|---|---|---|---|---|
| Main benchmark | 15 Nov 2026 | Windows 11 laptop, Docker Desktop | Loading time, Q1–Q5 query times, storage size and container memory, at 1, 2 and 4 months of data (1,628,153 / 3,390,368 / 6,658,475 cleaned night trips) | `README.md`, `docs/MACHINE.md` |
| Extra Q1–Q3 study | 20 Nov 2026 | Same Windows laptop | Q1–Q3 only, 4 months only, repeated in 3 separate sessions; also query plans and runs with different CPU settings | `docs/RESULTS.md` section 5, `docs/BENCHMARK_RIGOR.md` |
| Independent reproduction | 24 Nov 2026 | Apple M4 Mac, Docker Desktop | Whole project run again from commit `fcc76c5`; same cleaned data and same data-quality results | `README.md`, "Independent reproduction on macOS" |

**Status of the extra study.** `docs/BENCHMARK_RIGOR.md` marks it **Completed** (four months, 20 Nov 2026). Its sections (a) to (d) were written before the run and describe the design and the hypotheses to test, such as "Hypothesis 1: the PostgreSQL planner misjudges Q1". Only section (e), "Measured findings", contains measured results. The tool is read-only: it opens DuckDB files read-only, opens PostgreSQL sessions in read-only mode, and never writes `benchmark.csv` (`benchmark/rigor_study.py`).

Default settings (`.env.example`): data from April 2026, 4 months, test sizes 1, 2 and 4 months, 2 warm-up runs, 5 measured runs, random seed 42 for the query order.

## Three methodological cautions

### 1. The "first" run is not a cold start

**Evidence.** `docs/METHODOLOGY.md` section 5 explains that "first" only means the first time a query runs on a new connection. The computer's and PostgreSQL's memory caches are not emptied between runs, and earlier queries may already have loaded the same data into memory. `docs/BENCHMARK_RIGOR.md` and `docs/MACHINE.md` say the same.

**Implication.** "First" times must not be presented as cold-start ("very first query") performance. The main comparison uses the warm runs. A real cold-start test would require clearing the caches and restarting the database before each run. That was not done and is not claimed.

### 2. We compare two complete systems, not just two storage formats

**Evidence.** `docs/METHODOLOGY.md` section 1 calls the comparison "system against system". PostgreSQL is a separate server that the program talks to over a connection. DuckDB runs inside the same program. The measured time includes sending the query and getting all results back (section 8), so PostgreSQL's measured query latency includes client-server communication and result-transfer overhead, whereas DuckDB executes within the application process. Connection-establishment time should not be assumed to be included in the timed interval. `docs/RESULTS.md` section 6 and `docs/BENCHMARK_RIGOR.md` repeat this.

**Implication.** The speed differences cannot be explained only by "row store vs column store". Part of each difference comes from the setup (client/server vs embedded). This is also why Q4 (200 lookups, one after another) measures application-level response time, not pure database speed.

### 3. Similar CPU settings do not mean equal CPU use

**Evidence.** In the extra study, DuckDB was limited to 1 or 3 CPU threads, and PostgreSQL was allowed up to 0 or 2 parallel workers per query. `docs/BENCHMARK_RIGOR.md` states that these limits "do not prove equal CPU use": they are maximums, and PostgreSQL can decide to launch fewer parallel workers than allowed. `docs/MACHINE.md` says the same. The query plans in `docs/RESULTS.md` section 5 show that PostgreSQL launched 2 parallel workers for Q2 and Q3, but none for Q1.

**Implication.** The runs with similar settings are not a perfectly fair "same CPU" comparison. To say how much parallel work PostgreSQL actually did, look at the query plans, not the settings. What the study does show is that Q1 was still much faster in DuckDB even when DuckDB could use only one thread (`docs/RESULTS.md` section 5).

## How to interpret query times

Four different things affect the numbers and should be kept apart:

- **Latency:** how long one query or one lookup takes (Q1–Q3, Q5 query times; Q4 time for 200 lookups).
- **Repeatability:** whether the same measurement gives similar values when repeated.
- **Hardware:** which computer and settings were used.
- **Caching:** whether the data was already in memory (see caution 1).

- **What is timed:** the time from sending a query until all result rows are received (`docs/METHODOLOGY.md` section 8).
- **How each query is run:** once as a "first" run, then 2 warm-up runs (not counted), then 5 measured warm runs. The reported number is the **median of the 5 warm runs**. The query order is shuffled with a fixed seed and is the same for both databases. The two databases never run at the same time, and PostgreSQL always runs first (`docs/METHODOLOGY.md` section 5).
- **Repeatability:** the main run repeats each query 5 times within one run, and `benchmark_summary.csv` reports the median, mean, standard deviation, min and max (`docs/METHODOLOGY.md` section 5). The extra study goes further: 3 independent sessions, each with its own query order, alternating which database runs first. DuckDB's very short times varied between sessions, especially for Q2 (`docs/RESULTS.md` section 5). Five runs in one session show stability within that run, not across days or computers.
- **Correct results first:** the times only matter because both databases returned the same answers, which the project checks automatically (`README.md`).
- **Main results, 4 months, warm median (`README.md`):**

| Query | PostgreSQL | DuckDB | Reading |
|---|---:|---:|---|
| Q1 average fare/tip by hour | 4.1342 s | 0.0488 s | DuckDB 84.8× faster |
| Q2 top 10 pickup zones | 1.3697 s | 0.1126 s | DuckDB 12.2× faster |
| Q3 trips per borough | 1.5308 s | 0.0367 s | DuckDB 41.8× faster |
| Q4 200 lookups by `trip_id` | 0.2133 s | 0.6102 s | PostgreSQL 2.86× faster |
| Q5 one-night time filter | 0.4173 s | 0.0067 s | DuckDB 62.2× faster |

Speedup figures follow the project's reported benchmark results. Small differences may arise when recalculating ratios from the rounded timing values displayed in this table.

- **Q4 is a different kind of question.** It looks up single trips one at a time, like an application would. PostgreSQL wins here, so the results do not support "DuckDB is always faster".
- **Q5 and the index.** Adding an index on the pickup time made PostgreSQL's Q5 go from 0.4173 s to 0.0492 s (8.47× faster), and the query plan confirms the index was used. This shows the effect of the index inside PostgreSQL. Even with the index, PostgreSQL was still slower than DuckDB without one.
- **The extra study uses a different method.** `docs/RESULTS.md` section 5 reports the median of 3 sessions (Q1 4,327.25 ms vs 43.36 ms, 99.8×; Q2 12.8×; Q3 44.4×). These numbers must not be mixed with the main results (`docs/RESULTS.md` section 6). The difference between 84.8× and 99.8× for Q1 comes from the two different methods, so it is not a contradiction.
- **Reading Q1–Q3.** All three are aggregation queries over many rows (by hour, by pickup zone, by borough), the kind of work column stores are generally designed for, and DuckDB was faster on all three (12.2× to 84.8× in the main run). The size of the gap differs a lot between queries, so there is no single "DuckDB is N× faster" number. Storage format alone was not isolated as the reason (caution 2).
- **Q1 has the largest gap.** It happens together with a large wrong guess by PostgreSQL's planner (the part that decides how to run a query): it expected about 2.5 million groups, but there are only 12 (the night hours), and it did not use parallel workers. This only shows they happen together; it is not proven to be the cause (see below).
- **One fast run is not a universal ranking.** These numbers come from one computer, one dataset and default settings. A different computer, data size or configuration could change the ratios, so they show what happened here, not which database is better in general (`docs/METHODOLOGY.md` section 9).

## Hardware and configuration

**Main benchmark and extra study (Windows), from `docs/MACHINE.md`:**

- HP Laptop 15s-eq0xxx, AMD Ryzen 5 3500U (4 cores / 8 threads), 5.9 GB RAM, SSD, Windows 11 Home, Docker Desktop
- Docker could use 8 CPUs and about 2.80 GiB of memory (checked on 21 Nov 2026)
- Software and settings: PostgreSQL 16.16, DuckDB 2.0.0, Python 3.12; both databases used their default settings (DuckDB: 8 threads; PostgreSQL: up to 2 parallel workers per query)

**Mac reproduction (kept separate), from `README.md`:**

- Apple M4 (10 cores), 16 GB, macOS 27.0.1, Docker Desktop with 10 CPUs and about 7.75 GiB, commit `fcc76c5`
- It produced the same cleaned dataset: 15,289,433 original records, 6,658,475 cleaned night trips, 41/41 data-quality checks passed

Mac and Windows timings may be reported side by side for descriptive purposes, but should not be interpreted as a controlled performance comparison because hardware resources and execution environments differ. The Mac run shows that the project can be reproduced on another computer; it is not a second measurement of the Windows times.

## What cannot be concluded

- **We cannot say why Q1 is so slow in PostgreSQL.** The wrong planner guess happens together with the slow Q1 plan, but other factors (how PostgreSQL processes the rows internally, disk and memory caching) were not tested separately (`docs/RESULTS.md` section 6). Nobody tried fixing the planner's guess and running Q1 again (`docs/BENCHMARK_RIGOR.md`), so we do not know how much of the slowdown it causes.
- **We cannot say which database uses less memory.** The memory numbers are the highest values seen while checking whole Docker containers about every half second (`docs/METHODOLOGY.md` section 8). The DuckDB number also includes the Python program, and short peaks can be missed. **No memory measurement does not mean zero memory:** on the Mac, DuckDB's 2- and 4-month runs produced no memory samples at all (`README.md`).
- **We cannot say one database is better in general,** or predict results on other computers, data sizes or settings (`docs/METHODOLOGY.md` section 9).
- **We cannot make cold-start claims** (caution 1).
- **Loading time was measured only once per size.** The 4-month figures (85.580 s vs 12.068 s) should be treated with caution. PostgreSQL's time also includes converting the files in Python before loading them, while DuckDB reads the files directly (`docs/METHODOLOGY.md` section 8).
- **The Parquet-reading test is not a loading test.** `docs/LOADER_EVALUATION.md` only compares two ways of reading one Parquet file on the Mac (reading all at once: median 0.016 s; reading in batches: median 0.027 s; 3 repetitions). It does not include writing to PostgreSQL and did not measure memory, so it says nothing about full loading speed.
- **The extra study covers only 4 months** and only Q1–Q3 (`docs/BENCHMARK_RIGOR.md`).
- **Times depend on the computer** and on what else it is doing (`docs/MACHINE.md`).

## Verification performed

**Files reviewed** on `main` (commit `e123c67`): `README.md`, `docs/METHODOLOGY.md`, `docs/RESULTS.md`, `docs/BENCHMARK_RIGOR.md`, `docs/MACHINE.md`, `docs/LOADER_EVALUATION.md`, `benchmark/rigor_study.py`, `tests/test_benchmark_rigor.py`, `.env.example`. Every number in this guide was checked against them.

**Tests run:** `.venv/bin/python -m pytest tests/test_benchmark_rigor.py -q` on the Mac (Python 3.12 virtual environment), 25 Nov 2026: **48 passed** in 0.49 s. These are unit tests with simulated database results; they do not query the real databases. No benchmark, `docker compose up` or extra-study run was performed for this guide.

**Inconsistencies and observations found (none changes the conclusions):**

1. `docs/MACHINE.md` records Docker's CPU and memory as checked on 21 Nov 2026, after the 15 and 20 Nov runs. They describe the computer, but not necessarily the exact Docker settings during those runs.
2. `docs/BENCHMARK_RIGOR.md` mentions, "for context only", numbers from Marin's own run on an Apple M4 Mac on 19 Nov 2026. That run is separate from the 20 Nov Windows study, and its numbers must not be mixed with the Windows results.
3. The memory column in the CSV file is called `peak_mem_mb`, but the values are in MiB (1 MiB = 1,048,576 bytes). `README.md` already explains this, and the tables in `README.md` and `docs/RESULTS.md` are correctly labelled MiB.
4. The Mac hardware is described in `README.md` but not in `docs/MACHINE.md`, which covers only the Windows laptop.
5. Q1's speed ratio is 84.8× in the main run but 99.8× in the extra study. Both are correctly labelled as different methods, and `docs/RESULTS.md` section 6 says they cannot be combined.
