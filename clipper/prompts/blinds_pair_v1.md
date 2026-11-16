Gossip sites often blind the same event: two blind items from different sites, written differently, about the same person and the same thing that happened. You compare new blinds with recent ones from other sites and find those twins.

Each new blind below is numbered [i]; the other sites' recent blinds have an id in angle brackets. For each new blind that is clearly about the same event as one of the others, return:

- i: the new blind's number.
- id: the other blind's id, exactly as written between the angle brackets.
- why: one short sentence on what matches (the same claim, the same timing, the same specific clues).

Rules:

- Only the same event counts: the same claim about the same kind of person at about the same time. Two blinds that merely share a type of story (two affairs, two arrests) are not twins.
- Never guess or name who either one is about.
- At most one twin for each new blind; when unsure, return no pair for it.
- No twins at all is a fine answer: return an empty list.
