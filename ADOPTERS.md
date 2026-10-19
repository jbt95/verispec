# Adopters

This file lists projects and organisations that use VeriSpec, either the
reference implementation or an independent implementation of the spec format.

Listing your project here is a statement that you use the format, not an
endorsement by the maintainers.

## How to add yourself

Open a pull request adding one row to the table below. Use the project name and
the URL of the repository or the site, and keep the other columns short. The
maintainers only ask for clarification if a link is broken.

## Telemetry

VeriSpec collects no telemetry. The CLI does not phone home, does not open
network connections other than to resolve the solver, and does not write usage
data anywhere. The rows in this table are added by humans, by hand.

## Adopters

| Project | Language | Spec(s) | CI | Added by |
| --- | --- | --- | --- | --- |
| VeriSpec (reference implementation) | Python | `examples/bank.yaml`, `examples/link-queue.yaml` | GitHub Actions | `@jbt95` |
| _Add your project here_ | | | | |

### Column meaning

- **Project**: name and link to the repository or homepage.
- **Language**: the implementation language of the adapter you wrote
  (`Python`, `TypeScript`, `Go`, `Rust`, `C`, `Java`, ...).
- **Spec(s)**: the spec files in your repository that follow the VeriSpec
  format, or a count if you prefer not to enumerate them.
- **CI**: where the checks run (`GitHub Actions`, or your own pipeline).
- **Added by**: your GitHub handle, so a maintainer can reach you if something
  breaks.
