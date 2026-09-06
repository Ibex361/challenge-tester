# This shows how to use pacing.json

# It is good to use quick paces for the first **3** questions, because if the
# latter questions are harder and I used slowing paces for the easier
# first questions, it is worst of both worlds.

default is used for every question, unless overode below, to overide
a quesion's pacing, say that of one's, we use:

```

"1": 2 

```

- "1" stands for quesion 1.
-  stands for 2 seconds

{
  "default": 3,
  "1": 2.1,
  "2": 1.9,
  "3": 2.3,
  "4": 3.3,
  "5": 5.5,
  "max_quiz_seconds": 19.9
}

# This example is bounded theoretically from 15.1s - 19.9s, but it depends
# on performance of the AI etc.
