# AI Usage Disclosure

## Tools used
ChatGPT.

## What I used them for
I used ChatGPT to interpret the assignment requirements, design the initial project structure, generate implementation code, prepare tests, and draft documentation.

## What I wrote or decided myself
I reviewed the generated project, executed the setup and pipeline commands, inspected the generated reports, and checked the results of the included tests. The implementation was AI-assisted.

## Where AI was wrong or unhelpful
The initial project package did not include the assignment-a-data and docs directories required by the generator. I cloned the original starter repository and copied those directories into the project before running the pipeline.

## How I verified AI-generated code
- Generated synthetic data using seed 42 and small scale.
- Ingested drop_1 and drop_2 successfully.
- Generated all five required reports.
- Ran pytest: 4 tests passed.
- Re-ingested both drops and confirmed that report file hashes did not change.

Full hidden-seed output parity and fresh-seed integration testing have not yet been verified.