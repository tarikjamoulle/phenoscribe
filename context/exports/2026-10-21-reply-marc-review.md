Subject: Re: Phenoscribe Update

Hi Marc,

Thank you for checking 100 rows by hand. That is exactly the feedback the tool needs.

Your 8 corrections split into three groups.

**Five are real tool errors. I fixed them.**

- Tinnitus (MGA.404). The tool picked "Pulsatile tinnitus" because it was told to prefer the most specific term. It now picks a more specific term only when the patient's words support it. Later in the same recording the patient says "acouphène pulsatile", so the new file has both Tinnitus and Pulsatile tinnitus.
- Concentration (MGA.406). You're right: in HPO, "concentration" is always chemical. The search did not offer "Diminished ability to concentrate" (HP:0031987) for this phrase, so the tool showed the closest wrong term. It now leaves the code empty instead.
- The "car accident" and "driving" rows (MGA.404). The AI rejected all five candidates for both, and the tool then still showed the first one. That is how "Exploding head syndrome" and "Impaired smooth pursuit" got there. A rejected row now has an empty code with REVIEW. The tool also stops treating "like after my car accident" as a symptom. Vertigo was already coded as its own row for MGA.404.
- Chest pain (MGA.404). Denied symptoms now get their code, marked Absent, as you asked: Chest pain, HP:0100749, Absent.

**One is a limit of HPO.**

- Ear fullness (MGA.061). HPO has no term for "ears feel blocked". "Blocked Eustachian tube" (HP:0040269) is the nearest term. Would you code it like that?

**Two codes in your list name other terms.** I checked both on hpo.jax.org:

- HP:0002907 is "Microscopic hematuria". Flank pain is HP:0030157, which is the code in the file. https://hpo.jax.org/browse/term/HP:0030157
- HP:0031466 is "Impairment in personality functioning". HPO has no "white tongue" term, so "White oral mucosal macule" (HP:0033026) is the nearest. https://hpo.jax.org/browse/term/HP:0031466

This matches what ChatGPT told you: it gives wrong codes now and then. The five codes it checked for you (HP:0002381, HP:0000622, HP:0010632, HP:0033710, HP:0006688) come from our MGA.404 rows, and all five were correct.

On your 100 rows, that makes about 94 that agree with your reading. Three of the five real errors already had REVIEW, so the flag points to the right rows.

One question: which 100 rows did you check? With the list I can measure the agreement rate exactly, and use your 92 confirmed rows to make the Confidence number meaningful.

I'll send a new results.xlsx with these fixes.

Tarik
