"""Starter keyword list (English + Gulf Arabic). Each school reviews and edits
its own copy from the dashboard; new slang can be added at any time and is
pushed to devices on their next config sync.

Weights are optional; ambiguous everyday words get a low weight so they only
matter in combination with other evidence.
"""

# (term, lang, category, weight or None for the category default)
DEFAULT_KEYWORDS: list[tuple[str, str, str, float | None]] = [
    # --- drug names ---
    ("cannabis", "en", "drug_name", None), ("marijuana", "en", "drug_name", None),
    ("hashish", "en", "drug_name", None), ("cocaine", "en", "drug_name", None),
    ("captagon", "en", "drug_name", None), ("tramadol", "en", "drug_name", None),
    ("lyrica", "en", "drug_name", None), ("xanax", "en", "drug_name", None),
    ("ecstasy", "en", "drug_name", None), ("mdma", "en", "drug_name", None),
    ("ketamine", "en", "drug_name", None), ("heroin", "en", "drug_name", None),
    ("thc", "en", "drug_name", None), ("meth", "en", "drug_name", None),
    ("حشيش", "ar", "drug_name", None), ("مخدرات", "ar", "drug_name", None),
    ("كبتاجون", "ar", "drug_name", None), ("ترامادول", "ar", "drug_name", None),
    ("ليريكا", "ar", "drug_name", None), ("كوكايين", "ar", "drug_name", None),
    ("هيروين", "ar", "drug_name", None), ("ماريجوانا", "ar", "drug_name", None),
    ("حبوب", "ar", "drug_name", 0.2),
    # --- street slang ---
    ("weed", "en", "drug_slang", None), ("kush", "en", "drug_slang", None),
    ("zaza", "en", "drug_slang", None), ("joint", "en", "drug_slang", 0.3),
    ("blunt", "en", "drug_slang", None), ("spliff", "en", "drug_slang", None),
    ("molly", "en", "drug_slang", None), ("thc cart", "en", "drug_slang", None),
    ("edibles", "en", "drug_slang", None), ("the plug", "en", "drug_slang", None),
    ("stash", "en", "drug_slang", 0.3), ("coke", "en", "drug_slang", 0.2),
    ("شبو", "ar", "drug_slang", None), ("ابو هلالين", "ar", "drug_slang", None),
    ("الصنف", "ar", "drug_slang", 0.3), ("جوينت", "ar", "drug_slang", None),
    ("بانجو", "ar", "drug_slang", None),
    # --- deal phrases ---
    ("how much", "en", "deal_phrase", None), ("bring the stuff", "en", "deal_phrase", 0.35),
    ("pay me later", "en", "deal_phrase", None), ("cash only", "en", "deal_phrase", None),
    ("hook me up", "en", "deal_phrase", None), ("how many grams", "en", "deal_phrase", 0.4),
    ("front me", "en", "deal_phrase", None),
    ("كم السعر", "ar", "deal_phrase", None), ("بكم", "ar", "deal_phrase", 0.15),
    ("جيب الغرض", "ar", "deal_phrase", 0.35), ("ادفع بعدين", "ar", "deal_phrase", None),
    ("بعطيك بعدين", "ar", "deal_phrase", None),
    # --- hiding / warning phrases ---
    ("teacher is coming", "en", "hiding_phrase", None), ("hide it", "en", "hiding_phrase", None),
    ("put it away", "en", "hiding_phrase", None), ("someone is coming", "en", "hiding_phrase", None),
    ("watch the door", "en", "hiding_phrase", None), ("open the window", "en", "hiding_phrase", 0.15),
    ("المدرس جاي", "ar", "hiding_phrase", None), ("الاستاذ جاي", "ar", "hiding_phrase", None),
    ("المعلم جاي", "ar", "hiding_phrase", None), ("احد جاي", "ar", "hiding_phrase", None),
    ("خبه", "ar", "hiding_phrase", None), ("خشه", "ar", "hiding_phrase", None),
    ("طفها", "ar", "hiding_phrase", None),
]
