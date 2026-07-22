# Concepts
* Commodity Price lookup.
  * Any commodity
  * Filter by Station, Orbit, or System
  * Filter by distance from Station, Orbit, or System
  * Filter by highest sell price or lowest purchase price
* Item Price lookup (ship/personal items — weapons, armor, components, tools).
  * Same filters as Commodity Price lookup
* Vehicle Purchase lookup.
  * Same filters as Commodity Price lookup
* Vehicle Rental lookup.
  * Same filters as Commodity Price lookup
* Refinery Yield Bonus lookup.
  * Resolves either the raw ore or refined material name
  * Filter by Station, Orbit, or System
  * Filter by distance from Station, Orbit, or System
* Mining Location lookup (raw ore / harvestable spawn locations — planets, moons, Lagrange-point
  orbital sites, and points of interest like asteroid belts).
  * Resolves either the raw/harvestable form or the refined material name
  * Filter by Orbit (planet), Moon, or System
* Timer / Notification (e.g. AutoLoad cargo waits — start a timer, go do something else, get
  notified when it's done instead of sitting at the terminal watching it load).
  * Natural-language duration ("15 minutes", "in 15 minutes")
  * Needs a proactive-notification path — the tool call returns immediately, but the actual
    notification fires later, outside the request/response turn that started it. Straightforward
    in the push-to-talk voice loop (just speak when the timer fires); needs more thought in
    Chainlit, which doesn't have an obvious way to push a new message into a session from a
    background task without holding onto that session's context.
  * Query remaining time mid-wait ("how much longer until the cargo is loaded?") — returns a
    formatted mm:ss countdown. Ordinary request/response, no proactive-notification problem to
    solve — just needs the timer's start time + duration held somewhere queryable.

# Done
What's the price of Iron in Stanton? (Commodity)(Station, Orbit, System)
What's the best price of Iron within 30 Gm of Crusader? (Commodity)(Distance)
Where can I find Trawler Scraper Modules in Stanton? (Game Item)(Station, Orbit, System)
Where can I buy a Cutlass Black? (Vehicle Purchase)(Station, Orbit, System)
Where's the cheapest place to rent a 100i near Crusader? (Vehicle Rental)(Distance)
Which Refinery has the best boost for Iron in Stanton? (Refinable material)(Orbit, System)
Where can I mine Iron in Stanton? (Minable)(Planet, Belt)
Can I find Copper on Lyria? (Minable, Harvestable)(Planet, Orbit, System)

# To Do
Start a timer for 15 minutes. (Timer)(Notify — voice required, Chainlit nice-to-have)
Notify me in 15 minutes. (Timer)(Notify — voice required, Chainlit nice-to-have)
"They're loading the cargo, it'll take 15 minutes. Let me know when it's done." (Timer)(Notify, spoken proactively when it fires)
How much longer until the cargo is loaded? (Timer)(Query remaining time — mm:ss, ordinary request/response)