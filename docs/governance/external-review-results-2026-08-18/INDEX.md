# Formal external review results — 2026-08-18

```
FORMAL_EXTERNAL_REVIEW_RESULTS
FOUR_SEPARATE_VERDICTS
NO_BLOCKER_CLEARED
FREEZE_V4_13_ACTIVE_0_RESOLVED
MILESTONE_M0_COMPLETE_FALSE
```

This directory records four separable external-review verdicts.
It does not modify `specification-freeze-policy-v4.json`, owner
mandate values, implementation code, model outputs, or Linear statuses.
It does not clear any Freeze V4 blocker.

Raw reviewer output is retained. This index does not replace those
outputs with a later summary. Reviewer oracle scripts are stored as
`.py.txt` so archived recomputation code is not treated as shipping
source; the file bytes are the original scripts.

## Recorded dispositions (from each verdict file; not re-decided here)

| artifact | disposition | verdict grouped SHA-256 | reviewed commit | reviewed tree |
|---|---|---|---|---|
| A1 | GO | `ca1177b9:4a05a2ea:bbf48c20:60f68eb2:918777dd:f4a6e3ef:e01e9518:503b5aa1` | `d890078803c58f3ca995ff80004b025583fe6b2e` | `0d00c7b1ac87409c67ec32cbd0cde29c316d8334` |
| A2-V2 | GO | `ec9a1c44:a886e530:a1a4ca27:525d7fdd:e6238280:c1d60246:f3d0c9d1:631e034f` | `4848a7f899624288ad0d34ef3bce47070de0e1f5` | `d911bf583c748aac9aba76bb5c69045a08f17564` |
| A3-V2 | GO | `94953898:189944b8:c20ca2b7:eb5d4039:7472f219:e922c176:dc38adf3:a1a6a373` | `4848a7f899624288ad0d34ef3bce47070de0e1f5` | `d911bf583c748aac9aba76bb5c69045a08f17564` |
| A4 | GO | `1ad88520:96e09bd3:4396e666:1af57fd6:5a134f8f:4d007c57:38db0f52:a8c97fe7` | `d890078803c58f3ca995ff80004b025583fe6b2e` | `0d00c7b1ac87409c67ec32cbd0cde29c316d8334` |

## Reviewer identity (all four sessions)

- reviewer_provider: `xAI`
- reviewer_model: `Grok Build`
- reviewer_exact_revision: `UNAVAILABLE_NOT_EXPOSED_BY_PROVIDER`
- Four separate sessions; one artifact each.
- Codex was not available in this review environment; four
  independent non-Claude Grok sessions were used instead.

## Required statement

No empirical performance, capacity value, production readiness,
blocker clearance, or live-order authority is inferred by this
review.

## File inventory (grouped SHA-256 of committed bytes)

| path | grouped sha256 | bytes |
|---|---|---|
| `A1/A1-VERDICT.md` | `ca1177b9:4a05a2ea:bbf48c20:60f68eb2:918777dd:f4a6e3ef:e01e9518:503b5aa1` | 7892 |
| `A1/METADATA.md` | `f94a9ffa:04e472c7:521c799b:4786d0c3:be53f602:58937ca4:2c8caafc:b9482d66` | 1517 |
| `A1/RAW-TRANSCRIPT.md` | `3aaa8f85:fcbaa5f4:b718dc8b:8d49706e:3718bf6c:1763ed61:981f14ba:8eaa1c12` | 10965 |
| `A1/REVIEW-PROMPT.md` | `5f64ff5c:bd4cdab9:9de2580d:aea6570f:e33f97d6:2a314972:47c6f90f:643ca522` | 4585 |
| `A1/packet/HANDOFF-ADDENDUM.md` | `9f21825f:5be92b66:2a64146b:97a00482:1b30a282:3fcc5bdc:7b4eaeb0:147d4979` | 1785 |
| `A1/packet/PACKET.md` | `8857f3c1:bb04fc06:d48cad37:53c1a3ed:4bcd00b2:e329c7ff:cb80beb0:e20ed3d5` | 5680 |
| `A1/packet/REVIEW-PROMPT.md` | `5f64ff5c:bd4cdab9:9de2580d:aea6570f:e33f97d6:2a314972:47c6f90f:643ca522` | 4585 |
| `A1/packet/VERDICT-BLANK.md` | `dce72fb0:ce8dbd88:51a6556a:c1525c47:0bd662ea:633946a0:7ce3a0b2:a7de16ba` | 553 |
| `A1/production_verifier_supplement.output.txt` | `bf8cf2a6:d8aab122:380ed7f3:58e147c2:61ed56d8:ce578314:f01d8231:59158134` | 133 |
| `A1/pytest_supplement.output.txt` | `9da98bee:2baafdb6:2736d4a1:2183864c:e8d1806d:790d91a8:befab710:c87513d7` | 113 |
| `A1/recompute_a1.output.txt` | `eadeec2e:17aa6f2e:e3afdee7:18a6d819:c5daabed:bb0be539:1068465e:07cd003a` | 16629 |
| `A1/recompute_a1.py.txt` | `6207e16b:40e225b5:ba81a3d0:c96b556a:157ef38d:e3a6dc5e:1e92233b:c4028858` | 44106 |
| `A1/tamper-copies/claim-true-alpha_proven.json` | `fc1350ea:4f0d0ba3:9b2339ff:6015e83d:c7794ca7:2b3a6077:56eeb4df:ad2748f7` | 14090 |
| `A1/tamper-copies/claim-true-any_freeze_v4_blocker_cleared.json` | `98465312:8b9cf39c:ac176223:5825cef1:779be820:6b8cbfe5:bf142cf7:e1200248` | 14090 |
| `A1/tamper-copies/claim-true-data_spine_start_authorized.json` | `0e99a01d:9fcdb5bc:5d9e8c20:1c9bce95:cf0fd611:959226ed:562d8574:47f49de4` | 14090 |
| `A1/tamper-copies/claim-true-empirical_capacity_available.json` | `479c8352:2e84571b:196a4478:753514c5:f447ec1f:451c0f96:7b4e5a14:d93d4c0f` | 14090 |
| `A1/tamper-copies/claim-true-empirical_performance_available.json` | `a9ffef46:e7b34a62:c066433c:a8b61ae5:49af3e48:b325a6e4:fdf87a35:af0adbf2` | 14090 |
| `A1/tamper-copies/claim-true-milestone_m0_complete.json` | `f6f517db:eb52b8b1:395becd0:09169642:180854e6:11d7821a:ad1f5d5e:1d1802f7` | 14090 |
| `A1/tamper-copies/claim-true-portfolio_capacity_usd_claimed.json` | `c50683df:7af2e869:3d9e2316:1138bf7a:09dd8c10:8fbaaccd:48890139:a597f1a6` | 14090 |
| `A1/tamper-copies/claim-true-production_pit_data_spine_complete.json` | `f829f4b9:6e89d172:1125ef8d:cb95baa7:bdc5eb88:37c82a26:1771e808:60d292e1` | 14090 |
| `A1/tamper-copies/claim-true-production_ready.json` | `264d6307:fdc4aef5:a346b188:85e46cb9:aef124a9:8b0f5b80:3ace6c6f:b2a827b5` | 14090 |
| `A1/tamper-copies/claim-true-prospective_receipt_verified.json` | `48fea5c5:cbc4cffc:56c99ab5:6183b9bf:c9f77e89:ca7daf8d:01d4cfb8:d4e03dcb` | 14090 |
| `A1/tamper-copies/forged-semantic.json` | `7be18f42:3360af39:dba4052e:b737f235:e61930a4:9ee2af79:15855658:2711db31` | 16474 |
| `A1/tamper-copies/invented-timestamp.json` | `bb9de7a8:01bffede:896541ec:76b5bccd:d8ae31d3:6d1211df:e4abc641:f7d65e96` | 14109 |
| `A1/tamper-copies/mutated-capacity-solver.py.txt` | `e15f2564:c7035584:51521f80:90a09a44:dfa11a68:997185dc:01e628ae:0e4a167d` | 14225 |
| `A1/tamper-copies/mutated-config.json` | `2be489e1:da10b685:b844a0ff:08039a5e:abed939f:b8ecbd70:bf165673:f48a90cd` | 16474 |
| `A1/tamper-copies/mutated-manifest.json` | `22af4404:7e2eae4b:7078c398:ddefda17:3df4132b:56115cc4:e5a8fc08:d0771b6e` | 1028 |
| `A1/tamper-copies/mutated-schema.json` | `732b3bb0:e5d0d08f:562ee832:8c5a07b3:ff8dee67:7aecd1b0:5134dde0:7cb9d4e7` | 14485 |
| `A2-V2/A2-V2-VERDICT.md` | `ec9a1c44:a886e530:a1a4ca27:525d7fdd:e6238280:c1d60246:f3d0c9d1:631e034f` | 8139 |
| `A2-V2/METADATA.md` | `eef89f74:b2280bf5:6400f88d:fbcd82d3:1c8898b2:ecb7cfd6:7e8ad115:1a34cde8` | 1843 |
| `A2-V2/RAW-TRANSCRIPT.md` | `cdae0a52:d4207bc4:04a55cb1:de0c71dd:a2417b0c:5c280328:f0404249:30e406c2` | 16913 |
| `A2-V2/REVIEW-PROMPT.md` | `d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f` | 4744 |
| `A2-V2/independent_inference_oracle.json` | `55cefd32:3767d692:a33676db:4419ff4a:6d1938c7:8dc57ee8:06bbde05:ce2a0d3d` | 8963 |
| `A2-V2/independent_inference_oracle.output.txt` | `749f441e:d70e379a:1eaced8a:ac3557b5:8713e07c:fb6b778c:67097789:408ba685` | 8075 |
| `A2-V2/independent_inference_oracle.py.txt` | `f6f1d42f:fbb9adc2:055fd10b:738596d4:8a32dd4d:8e72f6c1:a2ceab19:54938196` | 34967 |
| `A2-V2/packet/HANDOFF-ADDENDUM.md` | `b0cb4474:abc3a459:4efbd73a:c0462952:5cc602df:394d7760:227d3a6e:65c477e3` | 1272 |
| `A2-V2/packet/PACKET.md` | `964ff9a2:40d5aa19:50b0c141:60439a21:e45becd6:1a78c1ae:d2e59029:1083e299` | 5819 |
| `A2-V2/packet/REVIEW-PROMPT.md` | `d1686ff2:5df07ad5:0659e035:e316b660:fd6022ad:a3d97dfe:e1f04333:f9d7541f` | 4744 |
| `A2-V2/packet/VERDICT-BLANK.md` | `dce72fb0:ce8dbd88:51a6556a:c1525c47:0bd662ea:633946a0:7ce3a0b2:a7de16ba` | 553 |
| `A2-V2/production_comparison.output.txt` | `bab1bece:33e90c20:df79bc4f:5aca65a6:4c87dd77:2e0ad084:46ee1f66:e766b3f5` | 6617 |
| `A2-V2/production_comparison.py.txt` | `ff8fcbb2:c90b5a7a:da8ca0fa:db503d2f:9c3bc3e0:329413fb:7374c1e7:f0cd4772` | 14075 |
| `A3-V2/A3-V2-VERDICT.md` | `94953898:189944b8:c20ca2b7:eb5d4039:7472f219:e922c176:dc38adf3:a1a6a373` | 5197 |
| `A3-V2/METADATA.md` | `e75f6cfa:442e6e95:725b027d:1a4ecac6:411a5518:8b14ab23:b0fc16ee:1aacce17` | 1267 |
| `A3-V2/RAW-TRANSCRIPT.md` | `0d843dc6:886a2ebf:574913dd:b129234c:b1be6193:f6b210e8:415cc4aa:16adee52` | 10788 |
| `A3-V2/REVIEW-PROMPT.md` | `d11f6bf9:78e2f3f1:d083852e:3de13c95:4334333f:e606c2c2:c52e064a:44c86e8b` | 4643 |
| `A3-V2/independent_capacity_solver.output.txt` | `f92ba90f:d9737e5e:0a8d6b14:191a6ff6:c3a7b5e2:08e17a7a:5242865d:67a7cdc5` | 9102 |
| `A3-V2/independent_capacity_solver.py.txt` | `19e5ff29:7caf41c5:93d68f4b:8514c669:388d734e:8f3bc29d:5ce31735:bdef6968` | 41880 |
| `A3-V2/packet/HANDOFF-ADDENDUM.md` | `07522f82:ff23633d:7f909dde:6e033a89:ed01b353:f2a41090:1f58f0c5:3d5f1c83` | 1004 |
| `A3-V2/packet/PACKET.md` | `37cbd436:1ed3ac50:d1ce75d0:b15dd5cd:850a7003:da6512f8:12889607:761ad710` | 5179 |
| `A3-V2/packet/REVIEW-PROMPT.md` | `d11f6bf9:78e2f3f1:d083852e:3de13c95:4334333f:e606c2c2:c52e064a:44c86e8b` | 4643 |
| `A3-V2/packet/VERDICT-BLANK.md` | `dce72fb0:ce8dbd88:51a6556a:c1525c47:0bd662ea:633946a0:7ce3a0b2:a7de16ba` | 553 |
| `A4/A4-VERDICT.md` | `1ad88520:96e09bd3:4396e666:1af57fd6:5a134f8f:4d007c57:38db0f52:a8c97fe7` | 6081 |
| `A4/METADATA.md` | `25b2f877:d0e9ae34:581c6a26:0ddce6c8:31250c4e:3082f3d3:d307318a:2de67b77` | 1272 |
| `A4/RAW-TRANSCRIPT.md` | `08e0aa81:5a26f4dd:e69c908c:4a639a65:6886dbc7:51ec4e20:4c46cc75:bc5f5a4d` | 9109 |
| `A4/REVIEW-PROMPT.md` | `314f7d31:0bc3fafb:215098e2:b5c184e8:6085335f:b6f2d1fa:82cf0c10:c2ae5f22` | 4846 |
| `A4/packet/HANDOFF-ADDENDUM.md` | `6d7ebd41:2601b1c3:154942c7:7fb6d901:0870558f:76a29d1c:3f6854ec:e1ed3dd9` | 1279 |
| `A4/packet/PACKET.md` | `efcd3068:6cbef941:d70cbbc3:70e02195:3f421636:68d674fc:c0ef7f88:cad3d8ee` | 6446 |
| `A4/packet/REVIEW-PROMPT.md` | `314f7d31:0bc3fafb:215098e2:b5c184e8:6085335f:b6f2d1fa:82cf0c10:c2ae5f22` | 4846 |
| `A4/packet/VERDICT-BLANK.md` | `dce72fb0:ce8dbd88:51a6556a:c1525c47:0bd662ea:633946a0:7ce3a0b2:a7de16ba` | 553 |
| `A4/verify_bound_hashes.output.txt` | `f96186c8:7f923c92:91b31df6:5d48176a:f0e45be4:da627c4c:8b818b37:05beb653` | 3407 |
| `A4/verify_bound_hashes.py.txt` | `8dfe7650:98da8288:ce8eda98:825a6277:18a7ed27:d6d765db:d15ba36a:f5f8a6a0` | 5194 |
| `A4/verify_calendar_structure_and_holidays.output.txt` | `d3a1a4b2:e6887460:ff44d013:0d5f0608:3aabcf60:4deb02d1:a18e91e5:9438e3dc` | 2798 |
| `A4/verify_calendar_structure_and_holidays.py.txt` | `2c861f30:e8010c15:fecf30bc:c6a8630b:d6216019:5d14d9c5:279b13b9:dc99ab8a` | 15117 |
| `A4/verify_linux_replay_evidence.output.txt` | `def2aa0e:7bd249e5:ffb46957:47b9952c:37d6fe16:fa37adb7:e7409d67:09d06a8f` | 2881 |
| `A4/verify_linux_replay_evidence.py.txt` | `7ea883cb:5931cb12:041966a2:72749a7a:4c010542:211e51de:463f8156:6bc6ae4e` | 4977 |
| `A4/verify_locks_and_wheels.output.txt` | `0506f137:7d6a8d35:fb3abee8:08637a30:2a96337b:f171cf42:ba0ece93:c8a2cea3` | 3833 |
| `A4/verify_locks_and_wheels.py.txt` | `c570f673:c48e0bae:e6e87c41:08015f2d:73da6bc2:c536947c:320d89a4:54c5ff2b` | 6951 |
| `A4/verify_regeneration_blocked.output.txt` | `94e59773:9bc65f23:d0b56254:a54e4457:1a576484:bc4855b1:89afdca0:fabf2157` | 817 |
| `A4/verify_regeneration_blocked.py.txt` | `a85e1376:da360b97:e4b9cb71:2b74aff2:4199377e:1022a605:9fd30ea4:f85e2a61` | 1313 |
| `A4/verify_xnas_official_cases.output.txt` | `aa6257fc:ddda06b4:17a7fee9:e8a85698:95cdd1fd:d0f4b7a0:6dca38da:9cb8491a` | 4896 |
| `A4/verify_xnas_official_cases.py.txt` | `04c36b10:cb210852:13f7d95c:5324b487:fed07ec5:4444ac5b:e0212161:de720105` | 7090 |
