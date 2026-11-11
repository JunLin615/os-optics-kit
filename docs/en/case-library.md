# The case library

[os-optics-kit](../../README.md) · English · [简体中文](../zh-CN/case-library.md)

Every case is a complete design that the released engine reruns and compares with its recorded outcome. Each case
carries a class, one to three domains, a provenance, a publication basis, a derived scale and a few tags, so the
library can be browsed and filtered: `os-optics-kit cases list --class lens`, `--domain`, `--provenance`,
`--publication`, `--scale`, `--tag`, `--catalog RELEASE|none`. `os-optics-kit cases vocabulary` prints every value
with its English and Chinese label. The exact file rules are in the [case format](../case-format.md).

## Classes

A case has exactly one class, chosen by its primary conclusion. A mechanical carrier alone does not change the class.

| Class | Meaning |
| --- | --- |
| Lens (`lens`) | A coaxial lens or lens group (prescription, ideal or tabulated lens model) whose conclusion is the lens's own behaviour: focusing, prescription, image quality. |
| Free-space platform (`free-space`) | A beam path laid out element by element in open space (expanders, relays, folds, splitters, polarization trains, stops) whose conclusion is that the path as assembled meets a concrete optical requirement and, with catalog parts, that it can be bought and built. |
| Integrated path (`integrated-path`) | A path held by custom mechanics (spectrometer, monochromator, packaged laser) whose conclusion is the joint validity of the internal optical path and its mechanical constraints. |

## Domains

One to three per case, the primary domain first.

| Domain | Covers |
| --- | --- |
| Imaging (`imaging`) | Image formation and image quality: spot, MTF, Strehl, PSF |
| Gaussian beams (`gaussian-beam`) | Propagation, focusing, expansion, waists |
| Diffraction (`diffraction`) | Wave optics, stops, Fourier optics, PSF and MTF from diffraction |
| Dispersion (`dispersion`) | Wavelength-dependent behaviour, chromatic focus |
| Polarization (`polarization`) | Polarization states and polarizing elements |
| Interference (`interference`) | Superposition of coherent fields |
| Quantum optics (`quantum-optics`) | Photon statistics and linear optical networks |
| Nanophotonics (`nanophotonics`) | Metasurfaces, RCWA |
| Beam shaping (`beam-shaping`) | Phase masks, flat tops, light sheets |
| Wavefront control (`wavefront-control`) | Aberration correction with modes |
| Beam steering (`beam-steering`) | Reflection geometry, beam position and pointing |
| Radiometry (`radiometry`) | Power on detectors, detection, measurement data |

## Scale

Scale is computed from the Project, never written by hand. Elements are the optical parts other than sources (a lens
group counts each lens; an aperture stop counts none); branches are beam splitters.

| Scale | Elements and branches |
| --- | --- |
| Small (`small`) | at most 3 elements, no branch |
| Medium (`medium`) | at most 12 elements, at most 2 branches |
| Large (`large`) | at most 40 elements, at most 8 branches |
| Very large (`very-large`) | anything larger |

## Provenance and publication

Provenance says where the design comes from: Literature (`literature`), Patent (`patent`), Manufacturer file
(`manufacturer-file`), Customer task (`customer-task`) or Self-built (`self-built`). Publication says why it may be
published here:

| Publication | Meaning |
| --- | --- |
| Own work (`own-work`) | Written for the kit, published under the repository's MIT license |
| Open license (`open-license`) | Third-party material under an open license, attributed |
| Permission (`permission`) | Published with the rights holder's written permission |
| Public domain (`public-domain`) | Material in the public domain |

A self-built case is always own work. Any other provenance names its source and a publication basis other than own
work.

## Images

Each case shows at most five images: one to three system views and at most three figures, each a PNG of at most
400 KiB. System views come from the engine itself, without a browser: `os-optics view RUN --no-browser --snapshot DIR
--views iso` for the 3D view and `--section ALL` for the 2D section of a lens. In a kit checkout,
`os-optics-kit cases images NAME` renders them and records them in `images.json`; run again without view options, it
redraws exactly the views recorded there. The first image is a 3D snapshot view (iso where it reads well), and a lens
case also shows its section. Figures are plots drawn by the case's own scripts; result tables stay Markdown tables.
Both READMEs show the same images in the same order and link each other.

The kit's `render` and `present` commands make presentation-grade figures, camera sets, galleries and animations; they
need `os-optics-kit[render]` and Chrome or Chromium and are never needed for case images.
