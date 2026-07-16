# Creative Edge module — Conversion & Creative Edge
Human-review driven (no computer vision). One brief in → 9 commercial outputs.

## Run
    python creative/creative_edge.py runs/<niche> --example   # write creative-brief.json
    # fill the brief (product facts + eyeball each competitor's images -> 0/1 per row)
    python creative/creative_edge.py runs/<niche>             # builds 8 reports + index
    python creative/creative_diagnosis.py runs/<niche>        # output 9, after launch (fill metrics.json)

## Outputs
Competitor Visual Matrix · Main Image Edge + concepts · Thumbnail Simulator ·
9-Image Storyboard + prompts · Embroidery Proof · Visual Consistency · Image Edge Score ·
Creative Experiments · Post-Launch Diagnosis.

## Rules enforced
- AI-generated embroidery is NEVER proof → proof MISLEADING blocks approval.
- A product-accuracy contradiction (wrong garment/hood/pockets) blocks approval regardless of score.
- Brand/character art is UNSAFE_TO_COPY, never an opportunity.
- Not every conversion problem is the image — diagnosis separates price/reviews/delivery/traffic.
