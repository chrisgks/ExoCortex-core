# References

The academic and intellectual sources ExoCortex relies on and synthesizes from.

This is a living document. Add an entry when a source genuinely shapes a design
decision, a claim, or a piece of writing — and say in one line *what we take from
it*, beyond the fact it exists.

## Augmentation & the extended mind — the "why"

- **[Vannevar Bush, "As We May Think"](https://www.theatlantic.com/magazine/archive/1945/07/as-we-may-think/303881/)**
  (The Atlantic, 1945). The Memex: an external, associative memory that extends
  the mind. The ancestor of the idea of an exocortex.
- **[J.C.R. Licklider, "Man-Computer Symbiosis"](https://doi.org/10.1109/THFE2.1960.4503259)**
  (IRE Transactions on Human Factors in Electronics, 1960). Human and machine as
  a coupled partnership, each doing what it is best at. The inner-loop /
  outer-loop split, sixty years early.
- **[Douglas Engelbart, "Augmenting Human Intellect: A Conceptual Framework"](https://www.dougengelbart.org/pubs/augment-3906.html)**
  (SRI Summary Report, 1962). The founding text: augment, do not replace;
  *bootstrapping* — get better at getting better. ExoCortex is a bet on this
  thesis, and tries to supply the control law Engelbart left unspecified.
- **[Andy Clark & David Chalmers, "The Extended Mind"](https://doi.org/10.1093/analys/58.1.7)**
  (Analysis, 1998). Cognition extends into the tools and notes you offload to.
  The philosophical basis for treating a plain-text system as part of the mind
  itself.

## Augmentation, not atrophy — the case against cognitive rot

The central wager: the same tools that can rot your thinking can sharpen it,
depending on whether they make you *do* the thinking or *skip* it. A feed and a
do-it-for-you assistant both remove the effort — and the effort is where you
grow. ExoCortex is built to force the productive part (externalizing, finishing)
and refuse to offload the part that makes you smarter. Anti-rot, by design.

- **[Slamecka & Graf, "The Generation Effect: Delineation of a Phenomenon"](https://doi.org/10.1037/0278-7393.4.6.592)**
  (Journal of Experimental Psychology: Human Learning and Memory, 1978).
  Information you generate yourself is retained far better than information you
  merely read. The empirical root of "writing it down is how it sticks."
- **[Bjork & Bjork, "Making Things Hard on Yourself, But in a Good Way: Creating Desirable Difficulties to Enhance Learning"](https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/04/EBjork_RBjork_2011.pdf)**
  (in *Psychology and the Real World*, 2011). The effort is not friction to
  remove — it is where durable learning happens. The basis for keeping the
  thinking hard on purpose while stripping the busywork around it.
- **[Risko & Gilbert, "Cognitive Offloading"](https://doi.org/10.1016/j.tics.2016.07.002)**
  (Trends in Cognitive Sciences, 2016). Pushing cognition onto external tools
  buys capacity but can cost the internal skill. The tradeoff ExoCortex tries to
  win: offload the busywork, never the thinking.
- **[Sparrow, Liu & Wegner, "Google Effects on Memory: Cognitive Consequences of Having Information at Our Fingertips"](https://doi.org/10.1126/science.1207745)**
  (Science, 2011). With information a click away, we remember *where to find it*
  instead of the thing itself. Frictionless retrieval quietly hollows out memory.
- **[Lee et al., "The Impact of Generative AI on Critical Thinking"](https://doi.org/10.1145/3706598.3713778)**
  (CHI 2025; Microsoft Research & Carnegie Mellon). Across 319 knowledge workers,
  the more they trusted the AI, the less they thought critically. Offloading
  judgment erodes judgment.
- **[Kosmyna et al., "Your Brain on ChatGPT: Accumulation of Cognitive Debt when Using an AI Assistant for Essay Writing Task"](https://arxiv.org/abs/2506.08872)**
  (MIT Media Lab, arXiv:2506.08872, 2025). EEG across four months: LLM-assisted
  writers showed the weakest neural engagement, the least ownership of their own
  work, and mounting *cognitive debt*. The clinical picture of the rot ExoCortex
  is built against.

## Dopamine & reinforcement learning — the "how"

- **[Montague, Dayan & Sejnowski, "A Framework for Mesencephalic Dopamine Systems Based on Predictive Hebbian Learning"](https://doi.org/10.1523/JNEUROSCI.16-05-01936.1996)**
  (Journal of Neuroscience, 1996). Dopamine as predictive, TD-style learning.
- **[Schultz, Dayan & Montague, "A Neural Substrate of Prediction and Reward"](https://doi.org/10.1126/science.275.5306.1593)**
  (Science, 1997). Dopamine *is* a temporal-difference reward-prediction error.
  The result that makes "optimize for dopamine with RL" rigorous rather than
  metaphor: ExoCortex closes an outer RL loop around the brain's own reward loop.
- **[Sutton & Barto, "Reinforcement Learning: An Introduction"](http://incompleteideas.net/book/the-book-2nd.html)**
  (2nd ed., MIT Press, 2018). The RL canon — reward design, credit assignment,
  the allocation policy.
- **[Lattimore & Szepesvári, "Bandit Algorithms"](https://banditalgs.com/)**
  (Cambridge University Press, 2020). The bandit starting point for the
  attention-allocation policy before full RL.

## Applying it to people — adaptive interventions, personalization, metareasoning

- **[Nahum-Shani et al., "Just-in-Time Adaptive Interventions (JITAIs) in Mobile Health"](https://doi.org/10.1007/s12160-016-9830-8)**
  (Annals of Behavioral Medicine, 2018). The closest deployed analog to
  ExoCortex's exact aim: use the person's state to decide *when* and *how* to act
  so they stay on track toward *their own* goal, adapting per-person over time.
  Systems like HeartSteps use contextual bandits for this — RL keeping you on
  rails, for you.
- **[Li, Chu, Langford & Schapire, "A Contextual-Bandit Approach to Personalized News Article Recommendation"](https://doi.org/10.1145/1772690.1772758)**
  (WWW, 2010). LinUCB — the canonical bandit that learns what to surface to an
  individual. The mechanism, with the conventional reward (clicks).
- **[Chen et al., "Top-K Off-Policy Correction for a REINFORCE Recommender System"](https://doi.org/10.1145/3289600.3290999)**
  (WSDM, 2019). RL recommendation at scale (YouTube). Deployed proof that RL
  adapts to a user over time — but optimizing engagement, the reward ExoCortex
  deliberately inverts.
- **[Lieder & Griffiths, "Resource-rational analysis"](https://doi.org/10.1017/S0140525X1900061X)**
  (Behavioral and Brain Sciences, 2020). Cognition as the optimal use of limited
  computational resources — the cognitive-science form of "every problem reduces
  to resource allocation."
- **[Russell & Wefald, "Principles of Metareasoning"](https://doi.org/10.1016/0004-3702(91)90015-C)**
  (Artificial Intelligence, 1991). Reasoning about *which* computation or action
  is worth doing next — the nearest theory for ExoCortex's altitude: a policy
  over how you spend effort and use your tools, not over the task itself.

## Federated & collective learning — the flywheel

- **[McMahan, Moore, Ramage, Hampson & Agüera y Arcas, "Communication-Efficient Learning of Deep Networks from Decentralized Data"](https://arxiv.org/abs/1602.05629)**
  (AISTATS, 2017). Federated averaging (FedAvg): train locally, share model
  updates rather than data, aggregate centrally. The basis for "users pay back
  with their learned policy, never their data."
- **[Kairouz et al., "Advances and Open Problems in Federated Learning"](https://arxiv.org/abs/1912.04977)**
  (2021). The landscape survey — data heterogeneity (non-IID), personalization (a
  shared base policy plus local fine-tuning), and the honest privacy limits
  (shared model updates can still leak; hence secure aggregation / differential
  privacy).

## System design — the build

- **[John Ousterhout, "A Philosophy of Software Design"](https://web.stanford.edu/~ouster/cgi-bin/aposd.php)**
  (2018). Deep modules, small interfaces, clean seams. The lens applied to
  ExoCortex's own codebase.
