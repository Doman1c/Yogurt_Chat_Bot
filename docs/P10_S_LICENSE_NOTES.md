# P10-S — License and Model Notes

Checked 2026-08-25. This is a technical inventory, not legal advice.

| Project | Source-code declaration | Practical P10-S note |
|---|---|---|
| RVC WebUI | MIT plus project agreement/output-responsibility notice | Review bundled assets and every checkpoint separately. |
| Seed-VC | GPL-3.0 | Keep it as an external local runtime unless distribution obligations are reviewed. Hugging Face weights have separate terms. |
| Amphion / Vevo2 | MIT | Review each released pretrained model/data card separately. |
| voicepaw so-vits-svc fork | MIT | Fork, dependencies and compatible models may differ in license/terms. |
| DiffSinger | Apache-2.0 | Training data, dictionaries and pretrained packages need separate review. |
| SOME | MIT | README explicitly requires consent for recordings used in training. |
| Demucs | MIT | Repository is archived; model distribution and third-party audio rights remain separate. |

Rules for P10-S:

1. Use only songs, vocals, singing references, and training material the user
   owns or is explicitly authorized to process.
2. Do not commit audio, transcripts, checkpoints, embeddings, indexes, or
   private model paths.
3. A permissive repository license does not grant rights to a downloadable
   checkpoint, its training data, or a commercial song.
4. Do not expose an upstream WebUI/Gradio server to LAN, cloud, or browser.
5. Complete a separate review before packaging, distributing, or remotely
   serving any engine/model combination.

Official references: [RVC](https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI),
[Seed-VC license](https://github.com/Plachtaa/seed-vc/blob/main/LICENSE),
[Amphion license](https://github.com/open-mmlab/Amphion/blob/main/LICENSE),
[so-vits-svc fork](https://github.com/voicepaw/so-vits-svc-fork),
[DiffSinger](https://github.com/openvpi/DiffSinger),
[SOME](https://github.com/openvpi/SOME), and
[Demucs](https://github.com/facebookresearch/demucs).
