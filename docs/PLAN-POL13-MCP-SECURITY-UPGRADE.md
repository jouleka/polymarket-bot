# PLAN — POL-13 MCP security upgrade

1. Add a failing contract test for the patched MCP 1.28.1 floor and observe the intended RED.
2. Update the fail-closed profile verifier, project pin, stopped installer pin, and valid-version
   fixtures with the minimum exact-version change.
3. Prove Hermes 0.18.2 compatibility in an isolated environment, then run focused and canonical
   suites using MCP 1.28.1.
4. Update current ticket/handoff evidence without rewriting historical verification records.
5. Run independent specification/security review and isolated pin/version-check mutations.
6. Publish and merge the reviewed branch. Stop Hermes then POL-17, preserve and verify production
   state, update both existing venvs, run exact-five preflight, and restart POL-17 then Hermes.
7. Verify live readiness, cron, memory, zero restarts, database/evidence integrity, and zero
   authority expansion; update verification evidence and YouTrack.

