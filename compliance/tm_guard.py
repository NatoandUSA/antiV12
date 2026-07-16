#!/usr/bin/env python3
"""
Shared trademark / IP guard for the pipeline. Default-to-CAUTION design:
  HIGH    = contains a known brand/franchise  -> EXCLUDE, never use.
  CAUTION = contains an unknown token (not common apparel/gift English)
            -> likely a brand/name/place; a HUMAN must verify before using.
  OK      = all tokens are recognized safe vocabulary.
IP risk outranks SEO. Over-flagging (CAUTION) is safe; missing a brand is not.
NOT legal advice — verify at tmsearch.uspto.gov.
"""
import re

# ---- known brands/franchises (expanded incl. the misses caught in review) ----
KNOWN_BRANDS = {
 "disney","mickey","minnie","marvel","avengers","spiderman","spider man","batman","superman",
 "dc comics","star wars","yoda","baby yoda","grogu","mandalorian","harry potter","hogwarts",
 "pokemon","pikachu","hello kitty","sanrio","barbie","bluey","paw patrol","peppa","minions",
 "grinch","dr seuss","sesame","elmo","mario","luigi","nintendo","zelda","minecraft","roblox",
 "fortnite","stitch","lilo","frozen","elsa","anna","moana","encanto","toy story","buzz lightyear",
 "winnie the pooh","snoopy","peanuts","garfield","simpsons","rick and morty","family guy",
 "the office","friends tv","stranger things","squid game","wednesday","wednesday addams","cocomelon",
 "gabby","bluey","one piece","naruto","dragon ball","demon slayer","jujutsu kaisen","ghibli",
 "totoro","sailor moon","hunter x hunter","spongebob","scooby doo","looney tunes","bugs bunny",
 "care bears","my little pony","transformers","sonic","labubu","pop mart","smiski","sylvanian",
 # brands / retail
 "nike","adidas","jordan","puma","reebok","under armour","gucci","prada","versace","chanel",
 "louis vuitton","dior","hermes","burberry","rolex","cartier","tiffany","pandora","swarovski",
 "supreme","north face","patagonia","lululemon","columbia","carhartt","levis","wrangler",
 "starbucks","coca cola","coke","pepsi","mcdonalds","in n out","chick fil a","jeep","ford",
 "chevy","tesla","harley","john deere","stanley","yeti","hydroflask","lego","crocs","vans",
 "converse","apple","google","amazon basics","dr martens","ugg","birkenstock",
 # music / celebrities / bands
 "taylor swift","swiftie","eras tour","beyonce","drake","bad bunny","travis kelce","messi",
 "ronaldo","lebron","grateful dead","nirvana","metallica","ac dc","rolling stones","beatles",
 "kiss band","pink floyd","olivia rodrigo","sabrina carpenter",
 # sports leagues / teams
 "nfl","nba","mlb","nhl","fifa","olympics","super bowl","world cup","cowboys","yankees","lakers",
 "packers","chiefs","red sox","dodgers","49ers","eagles nfl","celtics","warriors","bulls",
 # institutions
 "fbi","cia","nasa","fda","us army","navy seal","marines",
}

# ---- safe vocabulary (recognized apparel / gift / pet / common query English) ----
SAFE = set("""
a an the and or of to for in on with your you my our we us their his her its i me they them this that
is are be it as at by from so no not do dont just all more most every any one two three set pack piece
shirt shirts tshirt tshirts tee tees sweatshirt sweatshirts crewneck crewnecks hoodie hoodies sweater
sweaters pullover pullovers jumper jumpers top tops tank long short sleeve sleeves zip quarter half mock
neck fleece cotton polyester poly blend soft warm warmer cozy comfy oversized fit relaxed unisex mens
womens women men man woman girl girls boy boys kids kid baby babies toddler youth adult plus size sizes
small medium large ladies gentlemen
black white red blue navy maroon beige grey gray green pink purple violet yellow orange brown tan cream
forest heather charcoal olive burgundy teal ivory gold silver rose lavender sage
mom mum mama momma dad papa daddy grandma grandpa granny nana nan pop pops wife husband girlfriend
boyfriend sister brother aunt uncle auntie niece nephew cousin family families friend friends bestie
nurse teacher doctor engineer coach student graduate bride groom couple couples wedding fiance mother
father parents grandparents pet parent owner
dog dogs cat cats pet pets puppy puppies kitten kittens pup pooch doggo kitty feline canine paw paws
print prints breed breeds fur furry fluffy rescue
gift gifts gifting giftable present christmas xmas holiday holidays halloween valentine valentines
easter thanksgiving birthday anniversary graduation memorial remembrance sympathy keepsake bereavement
in memory loss remembering
custom customized customised personalized personalised personalize monogram monogrammed embroidered
embroidery embroider name names named photo photos portrait picture design designs designed initials
funny cute adorable pretty lovely simple minimalist aesthetic vintage retro classic modern trendy bold
lover lovers life lifestyle mode squad club crew tribe era vibes vibe energy things era
love loved loving happy best better day days home house work working team teams new made make making
order ordered handmade hand stitched wear wearing worn matching match couple twinning
usa america american patriotic country apparel clothing clothes outfit wardrobe merch
merry santa reindeer snowman snow snowflake winter ugly spooky ghost pumpkin turkey spring summer
autumn fall wedding guest bridal floral flower flowers leopard cheetah hippo bunny teen teenager
heart hearts cupid lucky shamrock animal animals western cowboy cowgirl boho retro groovy
""".split())

_word = re.compile(r"[a-z']+")

def check(kw):
    """Return (status, reason). status in {'HIGH','CAUTION','OK'}.
    Consolidated: delegates to the maintained ip_guard engine when available so
    the whole pipeline uses ONE decision source (fixes 'stitch' etc. disagreeing).
    Falls back to the local logic below if ip_guard can't be imported."""
    try:
        import ip_guard   # lazy import avoids a circular import at module load
        return ip_guard.check(kw)
    except Exception:
        pass
    t = " " + kw.lower().strip() + " "
    for b in KNOWN_BRANDS:
        if f" {b} " in t or t.strip() == b:
            return "HIGH", f"known brand/franchise: '{b}'"
    unknown = [w for w in _word.findall(kw.lower())
               if len(w) >= 3 and w not in SAFE]
    if unknown:
        return "CAUTION", "verify unknown token(s) at tmsearch.uspto.gov: " + ", ".join(sorted(set(unknown))[:5])
    return "OK", ""

if __name__ == "__main__":
    import sys
    for k in sys.argv[1:]:
        print(f"{check(k)[0]:8} {k}  — {check(k)[1]}")
