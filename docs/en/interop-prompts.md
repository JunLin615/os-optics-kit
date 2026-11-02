# Prompts for your own agent: OpenSci with external scientific tools

[External tools guide](interop.md) · English · [简体中文](../zh-CN/interop-prompts.md)

Copy one template into your own coding agent and replace the parts in angle
brackets. Each template asks the agent to work in your environment, with your
installed tools, and to keep OpenSci results and external results apart. The examples
use os-optics **0.10.0**; follow the
[version matching instructions](installation.md#match-the-examples-to-the-installed-version).

The templates cover simulation and analysis only. Ordering, sending feedback and
uploading a design to a commercial cloud service are separate decisions you make
explicitly; nothing in these templates asks for them.

## Optimization and tolerance (SciPy, Optuna, SALib)

```text
Use OS Optics (the version required by the companion examples) with SciPy, Optuna or SALib in this Python
environment. My design: <Project file or description>. Goal: <metric, e.g. output divergence
half-angle in rad at plane "output">; variables: <component ids and fields, with ranges and units>;
tolerances to study: <fields and +/- ranges>.

1. Read the OpenSci agent guide and docs/en/interop.md of the os-optics companion repository,
   `os-optics --help`, `os-optics results metrics`, and the official documentation of the
   optimizer you choose.
2. State the objective as an OpenSci target metric with unit and selectors, the variables as Project
   fields edited by id, and which part OpenSci computes (each design) and which part the optimizer
   decides (the next values). Project retained_dofs are design intent, not optimizer variables.
3. Check the installed versions and that the Project runs once. Ask me only for missing values that
   change the computation (ranges, bounds, wavelength, required accuracy).
4. Run the loop: copy the Project, edit by id, run, read the metric from the run's results, log every
   evaluation with project_hash, input_hash, result_hash and engine_build_id. Keep selected-objective
   validity separate from requirement feasibility. Preserve invalid, partial, unmet and indeterminate
   evaluations; use only an exact valid objective for ranking, and report no feasible result explicitly.
   Follow the objective's actual model dependencies. Compare with an independent check such as paraxial ABCD.
5. Report separately: the OpenSci runs (statuses, issues, identities) and the optimizer's result
   (method, settings, seed, evaluations, best value, sensitivity indices with confidence intervals).
   Show the evaluation log and any plots as files.
6. If a tool is missing or an input is insufficient, say which step was the last one actually
   completed and what is needed next. Never report a value that was not computed.
```

## Diffraction and imaging (prysm, POPPY, LightPipes or Diffractio)

```text
Use OS Optics (the version required by the companion examples) with <prysm / POPPY / LightPipes / Diffractio> in
my environment. Layout: <Project file or description: source, stop or aperture, lens, observation
planes>. Question: <e.g. focal-plane peak irradiance and encircled power within 50 um>.

1. Read docs/en/interop.md of the os-optics companion repository, `os-optics results --help`
   and the field-construction convention it states, and the official documentation of the chosen
   diffraction package.
2. State which numbers come from OpenSci (Gaussian beam state, power, aperture geometry, plane frames)
   and which from the external package (the diffracted field). Intensity alone does not determine a
   complex field, and sparse rays are not a sampled pupil field: say which field you construct and
   under which assumptions (TEM00 with M^2 = 1, scalar, paraxial, ideal lens phase).
3. Check that the run reports M^2 = 1 on both axes before building a TEM00 field. Ask me only for
   missing physical inputs (stop size, distances, wavelength, required accuracy).
4. Run OpenSci, read the beam state and issues, build the sampled field, propagate it externally and
   run a sampling and window convergence check. Compare with an independent reference where one
   exists (for a round stop: the Hankel integral). Diffractio needs numpy < 2: run it in a separate
   environment that reads only the exported files.
5. Report separately: OpenSci's status, issues and Gaussian values, and the external result with its
   convergence spread. If OpenSci reports GAUSSIAN_TRUNCATION or OUT_OF_MODEL_SCOPE, use the external
   result for focal irradiance and say so. Show the profile plot and the files.
6. If a package is unavailable or a required input is missing, state the last completed step and the
   next one. Never fill missing fields with assumed zeros or defaults.
```

## Quantum photonics (Perceval, with Piquasso as cross-check)

```text
Use OS Optics (the version required by the companion examples) with Perceval in my environment, and Piquasso as an
independent cross-check if it is installed. Layout: <Project file or description>. Quantum inputs:
photons per source <...>, polarization mode <H/V>, wavepacket <shape, rms duration, zero-delay
indistinguishability>, detectors <type, efficiency, dark-count probability, which planes>.

1. Read docs/en/interop.md of the os-optics companion repository, `os-optics project network
   --help`, the conventions block inside the exported network, and the Perceval documentation for
   circuits, noise models and simulators.
2. State the split: OpenSci exports the linear optical network (modes, transfer matrix T with losses,
   paths with optical and group delays, conventions); Perceval computes photon statistics. Photon
   numbers, wavepackets, indistinguishability and detectors are my inputs: never derive them from the
   classical laser in the Project.
3. Check installed versions and licenses as reported by the packages, and that every quantum input is
   given. Ask me only for missing quantum inputs.
4. Export with `os-optics project network`, read T as T[output, input], embed a lossy T in a
   unitary (unitary dilation) including detector efficiency, set partial distinguishability from the
   photons' relative group delay, and run Perceval. Check that a single-wavelength T describes the
   photons (path group-delay spread much shorter than the wavepacket). Verify against a closed form
   where one exists (two-photon interference on a 2 x 2 network).
5. Report separately: the export (status, refusals, contract hash, engine build), the quantum inputs
   used, and Perceval's results with the deviation from the cross-check and the reference. Show plots
   and files.
6. If the export is refused or an input is missing, report the refusal codes or the missing input and
   stop there. Never invent quantum inputs or results.
```

## Metasurface response to a virtual focusing component (grcwa, meent, DFlat or metabox)

```text
Use OS Optics (the version required by the companion examples) with <grcwa / meent / DFlat / metabox> in my
environment to turn a metasurface lens response into an OpenSci TABULATED_THIN_LENS model and use it
in my layout. Metasurface: <materials, lattice, pillar family, design wavelength, focal length,
diameter>. Use range: <wavelengths, input beam size, field angles, polarization>.

1. Read docs/en/interop.md of the os-optics companion repository, the TABULATED_THIN_LENS fields
   in `os-optics schema project`, and the documentation of the chosen electromagnetic tool.
   metabox runs on Python 3.8: keep it in its own environment and exchange files.
2. State the split: the external tool computes the unit-cell responses and the focusing (with stated
   approximations, e.g. local periodic approximation, scalar propagation); OpenSci uses the fitted
   table as a thin focusing element inside its stated domain. One external focus does not define a
   general component.
3. Check installed tools and ask me only for missing physical inputs (materials, dimensions, the use
   range above).
4. Compute the response with a numerical convergence check on three separate sets: table
   wavelengths (the model rows), calibration cases (wavelengths between the rows, beam sizes and
   field angles to be supported) and test cases not used for anything else. Fit the rows; set the
   per-metric error bounds and the domain (wavelength span, max_field_angle_deg from OpenSci runs
   compared with the external focus, max_beam_radius_mm, validated polarizations) from the
   calibration set plus the convergence spread; record tool versions and the SHA-256 of the source
   data. Check the finished model in OpenSci on the test set only, and check that out-of-domain
   inputs give OUT_OF_MODEL_SCOPE.
5. Report separately: the external results (with convergence spread), the model and its domain, and
   OpenSci's runs (statuses, issues, identities) with their deviation from the external results. The
   virtual component is a simulation model, not an orderable product. Show plots and files.
6. If a tool is unavailable or the data cannot support a model within the needed accuracy, report the
   last completed step and keep the external result as a separate report. Never extrapolate the table
   or guess missing phases or efficiencies.
```
