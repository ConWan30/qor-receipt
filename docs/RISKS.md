# Residual risks (v0)

This is what a VERIFIED receipt does **not** prove. Platforms should price these in.

1. **Account sharing / pro-for-hire.** The receipt proves that *this agent key* drove *this controller* on a live
   PS5 feed. It doesn't prove *which human* held the controller. The PSN id is self-reported by the agent.
   Mitigations (later): PSN profile cross-check, cross-match behavioral input fingerprints, platform KYC.
2. **Both players collude** (e.g. to launder money or farm promos). Dual attestation only proves the two agents agree.
   Two colluding players can play a real but staged game, and every check passes. Out of scope for a result receipt.
   Platform-side risk controls are needed.
3. **Single-attested matches.** One agent and the referee. The opponent's side is unobserved, so a cheating
   single-attester controls both the video and the input evidence. That's why it's a lower tier.
4. **Compromised player PC (phase 1 software keys).** Malware or a skilled cheater with the key can *live*-forge
   checkpoints if they also fake capture and HID in real time. Referee timestamps stop *post-hoc* fabrication, not a
   real-time simulator with a nonce-responding bot. Raising that cost is phase 2 (TPM, sealed dongle).
5. **Video injection in real time.** An HDMI or capture-card MITM that composites a fake scorebug while the real game runs
   would pass the pause challenge (the pause is real). Cross-checks: dual-mode timelines; motion correlation; later, C2PA or
   a signed capture path.
6. **Scorebug misread.** The vendored reader refuses unless frames agree, but a new Madden UI patch can break the
   profile. The fail mode is "no final", not "wrong final". Watch out for profile drift after title updates.
7. **Disconnects / mercy rules / quits.** v0 detects only a normal 4th-quarter or OT final. Quits produce no receipt,
   and the platform's dispute flow applies.
8. **Referee trust.** The referee is a trusted third party. A compromised referee key can sign anything. Pin its key,
   rotate it, and publish receipts to a transparency log (phase 2).
9. **Clock and latency windows.** The 600 ms pause-latency and 3 s skew windows are first guesses. Tune them on real rigs.
   Loose windows weaken relay detection; tight windows cause false FAILs.
10. **Privacy.** Receipts carry hashes only, not frames or raw input. HID logs submitted for challenges are hashed into
    the receipt. The referee sees them in memory.
