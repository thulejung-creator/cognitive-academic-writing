# Revisable cognitive model

Use this reference when adapting explanations, argument support, or writing assistance to the author. Keep the model small and specific to the current task. Do not infer personality, intelligence, diagnosis, or stable competence from writing behavior.

## Entry contract

Each entry has:

| Field | Meaning |
| --- | --- |
| id | Stable identifier within the current task |
| dimension | Research goal, concept, position, evidence judgment, writing preference, or unresolved issue |
| statement | Narrow, actionable observation or preference |
| basis | User statement, observed text, or explicit AI hypothesis, with a location or turn reference |
| epistemic_status | User-stated, tentative inference, or unknown |
| lifecycle_status | Active, frozen, superseded, withdrawn, or unresolved conflict |
| scope | Task, section, concept, or time period where the entry applies |
| revision | Current revision and brief reason for correction |
| dependencies | Recommendations or passages that depend on this entry |

These fields describe working state, not a requirement to create a database or file. Use a compact table in the conversation when that suffices. A user-stated belief is not automatically a verified external fact.

## Update protocol

1. Add an entry only if it changes assistance in the current task.
2. Attribute the basis; do not turn silence or an accepted edit into agreement with a broader inference.
3. Make the model inspectable and correctable. Do not interrupt routine work for confirmation of every tentative entry.
4. Apply the user's correction immediately to the affected entry. Preserve earlier context only when needed to explain scope or an unresolved conflict.
5. Check dependent explanation depth, outline suggestions, and proposed wording. Do not rewrite unrelated passages or change source findings.
6. Stop using withdrawn or superseded entries. A frozen entry stays unchanged until the user directs otherwise; new conflicts can be reported separately.
7. On reset, stop using the former model. Explain that resetting active working state does not erase conversation history or provider logs.
8. At context limits, retain only permitted, relevant state. If state is lost, acknowledge the gap; do not claim continuity or reconstruct preferences as facts.

## Example

Observed text: "The survey proves that participation caused higher achievement."

Appropriate entry: "In paragraph 4, the causal wording exceeds the described observational evidence; offer a distinction between association and identification. Scope: this claim. Status: tentative inference from text."

Inappropriate entry: "The author cannot understand causality."

If the user explains that the wording was copied for criticism, withdraw the inference and reconsider any teaching recommendation based on it.

## Collaboration depth

- Direct execution: perform authorized work and briefly flag consequential decisions.
- Explanatory collaboration: explain evidence, alternatives, and tradeoffs.
- Cognitive guidance: expose concept relations and competing explanations; use exercises or questions only when welcomed.
- Review: evaluate the supplied text against explicit criteria.

Adapt the help, not the factual standard. Guidance must not become a prerequisite for receiving a usable result. Do not claim learning from AI-written prose, acceptance clicks, satisfaction, or a single corrected answer. Optional learning evaluation requires the user's participation and should examine explanation, transfer, and evidence discrimination.

## Persistence boundary

Default to the current task context. Keep skill installation, output-file permission, chat history, and persistent cognitive-profile consent separate. If persistence is requested, agree on fields, destination, access, duration, correction, and deletion before saving; do not infer these permissions from ordinary writing requests. Do not automatically import profiles into another task.

The optional project archive is for operational state only. Do not place cognitive entries, corrections, private history, or personal difficulties in project assumptions, evidence, or agreement text. See [Project coordination](project-coordination.md) for the three information domains.
