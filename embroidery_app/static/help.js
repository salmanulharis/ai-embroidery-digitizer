"use strict";
/* Plain-language help for every option + an accessible tooltip.
   Each entry: t = title, what = what it does, why = why it helps, eg = example. */

const HELP = {
  // ---------------------------------------------------------------- step 2: prepare design
  hoop: {
    t: "Hoop size",
    what: "The frame on your machine that holds the fabric tight while sewing.",
    why: "Picking your hoop sets a design width that fits inside it with a safe margin.",
    eg: "Choose “Left chest” for a small logo on a polo shirt.",
  },
  width_mm: {
    t: "Design width",
    what: "How wide the design will be on the fabric. The height adjusts automatically to keep the proportions.",
    why: "Size changes how much detail can be sewn and how many stitches are needed.",
    eg: "About 90 mm for a shirt-chest logo, 250 mm for the back of a jacket.",
  },
  num_colors: {
    t: "Number of thread colours",
    what: "How many different threads the design uses. “Automatic” finds the main colours for you.",
    why: "Fewer colours means fewer thread changes and faster sewing. Shading and gradients are turned into solid colours.",
    eg: "A red and black logo on a white background: Automatic, or choose 2.",
  },
  background: {
    t: "Background",
    what: "Removes the area around your design so only the logo is sewn.",
    why: "The plain backdrop of a picture is usually not meant to be embroidered – the fabric takes its place.",
    eg: "A logo on a white page: the recommended option removes the white around it but keeps white parts inside the logo for you to decide.",
  },
  min_feature_mm: {
    t: "Smallest detail to keep",
    what: "Lines or details thinner than this are smoothed away.",
    why: "Thread is about 0.4 mm thick – details thinner than that cannot be sewn cleanly and look messy.",
    eg: "0.7 mm suits most logos. Lower it (0.4) to keep fine details, raise it (1.2) to simplify a busy picture.",
  },
  min_area_mm2: {
    t: "Remove specks",
    what: "Tiny spots smaller than this area are removed.",
    why: "Dust, noise and tiny dots would each need their own stitches and thread cuts.",
    eg: "1 mm² is about the size of a 1 × 1 mm dot.",
  },
  denoise: {
    t: "Smooth out picture noise",
    what: "Evens out grainy or blotchy colours before the colours are detected.",
    why: "Photos and compressed JPG files have speckled colours that would otherwise become messy shapes.",
    eg: "Left (0) for clean digital logos, right (3) for photos or blurry images.",
  },
  knockout: {
    t: "Skip hidden parts",
    what: "Where shapes overlap, only the part you can actually see is sewn.",
    why: "Sewing hidden layers wastes stitches and makes thick, stiff bumps.",
    eg: "A white star on a blue circle: the blue under the star is not sewn.",
  },
  vector_preview: {
    t: "Design ready for stitching",
    what: "Your design simplified into solid colour shapes – this is what will be turned into stitches.",
    why: "Check it carefully before continuing: anything missing here will be missing in the embroidery.",
  },
  ai: {
    t: "AI helper (optional)",
    what: "Online AI services that can redraw a messy picture cleanly or give advice.",
    why: "Helps with photos or blurry logos. Not needed for clean logos and artwork files.",
    eg: "A phone photo of a printed T-shirt logo.",
  },
  aiProvider: {
    t: "AI service",
    what: "Which AI service to use. Only services you have set up appear here.",
    why: "Services differ in price and quality; some can only give advice.",
  },
  aiClean: {
    t: "Redraw cleanly",
    what: "The AI redraws your picture as clean, flat artwork.",
    why: "Removes blur, shadows and photo backgrounds. AI may change small details or text – always compare with your original.",
    eg: "Turns a blurry scanned logo into crisp shapes.",
  },
  aiAnalyze: {
    t: "Get advice",
    what: "The AI looks at your picture and suggests a number of colours and tips.",
    why: "A quick second opinion – it does not change your design.",
  },
  layers_sew: {
    t: "Stitch this colour",
    what: "Untick a colour to leave that area unstitched, so the fabric shows through.",
    why: "Saves stitches where the fabric already has the right colour.",
    eg: "White inside letters on a white shirt: untick White.",
  },
  layers_thread: {
    t: "Thread colour",
    what: "Click the swatch to pick the exact thread colour.",
    why: "Changes the preview and the colours saved in PES/VP3 files. Your machine uses the threads you load.",
  },
  layers_name: {
    t: "Name",
    what: "A label for this thread, shown on the worksheet for the machine operator.",
    eg: "“Navy 1966” to match your thread brand's number.",
  },
  layers_area: {
    t: "Area",
    what: "How much of the design this colour covers.",
    why: "Very small areas may not be worth a thread change – consider merging them.",
  },
  layers_stitch: {
    t: "Stitch style",
    what: "How this colour is sewn. Automatic chooses the best style for each shape.",
    why: "Fill: textured rows for large areas. Satin: smooth, shiny zig-zag for narrow shapes like lettering and borders. Outline: a single thin line.",
    eg: "Keep Automatic unless a shape looks wrong in the preview. To change just one shape, click it in the design.",
  },
  layers_angle: {
    t: "Stitch direction",
    what: "The direction of the stitch rows in filled areas of this colour, in degrees. Leave empty for automatic.",
    why: "Direction changes how light reflects off the thread, which can make areas stand out. To turn one shape (or satin shapes), select it in the design instead.",
    eg: "0 = horizontal rows, 90 = vertical, 45 = diagonal.",
  },
  layers_shapes: {
    t: "Shapes",
    what: "How many separate shapes this colour has, and how many have their own settings.",
    why: "“Select” picks all of them at once, so you can change their stitch style or direction together.",
  },
  shape_select: {
    t: "Change single shapes",
    what: "Click a shape in the design to select it, then choose its own stitch style or direction in the panel that appears.",
    why: "Lets you treat one part differently from the rest of its colour.",
    eg: "Make a logo's border run straight across while the letters stay automatic.",
  },
  show_directions: {
    t: "Show directions",
    what: "Draws light lines on each shape showing which way its stitches will run.",
    why: "Check the directions at a glance before creating stitches.",
  },
  undo: {
    t: "Undo",
    what: "Takes back your last change to the design (shape settings, colours, merging or “Update preview”).",
    eg: "Shortcut: Ctrl+Z (Cmd+Z on Mac).",
  },
  shape_style: {
    t: "Stitch style for the selected shapes",
    what: "How the selected shapes are sewn. “Same as colour” follows the colour list below; “Don't stitch” leaves the shape out so the fabric shows.",
    why: "Fill suits large areas, Satin gives a smooth shine on narrow shapes, Outline sews just the edge.",
    eg: "Remove a stray speck with “Don't stitch”.",
  },
  shape_direction: {
    t: "Direction for the selected shapes",
    what: "Which way the stitches run inside the selected shapes, in degrees. “Same as colour” uses the colour's (or automatic) direction.",
    why: "Different directions make neighbouring areas catch the light differently; the same direction everywhere gives a calm, uniform look.",
    eg: "0 = horizontal, 90 = vertical, 45 = top-left to bottom-right, 135 = bottom-left to top-right.",
  },
  shape_draw: {
    t: "Draw direction",
    what: "Drag across the design in the direction the stitches should run - the angle of your drag is used.",
    why: "Quicker than working out degrees: just follow the shape with your mouse or finger.",
  },
  shape_whole: {
    t: "Same direction for the whole design",
    what: "Gives every shape in the design the direction shown above.",
    why: "For a uniform look where all stitches run the same way. You can still change single shapes afterwards, or undo.",
    eg: "Satin that follows a curve (borders, lines) keeps following it.",
  },
  shape_thickness: {
    t: "Thickness for the selected shapes",
    what: "How tightly the stitches are packed in these shapes: the gap between fill rows or satin stitches. A smaller gap means thicker, fuller coverage.",
    why: "Make small details and lettering bolder, or save stitches on large background areas. “Same as design” uses the setting in step 3.",
    eg: "Light (0.50 mm) for big background areas · Normal (0.40 mm) · Dense (0.33 mm) for small lettering or towels where fabric shows through.",
  },
  shape_width: {
    t: "Extra width for the selected shapes",
    what: "Makes these shapes very slightly wider than drawn, to make up for the fabric pulling stitches inwards.",
    why: "Use it when narrow satin letters or borders come out too thin on the fabric.",
    eg: "0.3–0.4 mm for thin satin lettering on a stretchy polo shirt.",
  },
  shape_bold: {
    t: "Bold outline for the selected shapes",
    what: "Sews outline-only shapes and thin lines once (single) or three times over the same path (bold).",
    why: "Bold lines are thicker and stand out more – like going over a pencil line with a marker.",
  },
  shape_reset: {
    t: "Reset shape settings",
    what: "Removes the shapes' own settings so they follow their colour's settings again.",
  },
  shape_list: {
    t: "Choose shapes from a list",
    what: "Every shape listed by colour and size - pick one or more without using the mouse.",
    why: "Handy for tiny shapes that are hard to click, or when using a keyboard.",
  },
  shape_click3: {
    t: "Change shapes while previewing",
    what: "Click a shape in the stitch preview to select it, then change its stitch style or direction below. The stitches update automatically.",
    why: "See the effect of a change straight away.",
  },
  layers_order: {
    t: "Sewing order",
    what: "Colours are sewn from top to bottom. Use the arrows to change the order.",
    why: "Large background areas should go first and small details last, so details sit on top.",
  },
  layers_merge: {
    t: "Merge into",
    what: "Combines this colour into another one, so both are sewn with one thread as one smooth area.",
    why: "Fewer thread changes, and no seams between similar shades.",
    eg: "Two shades of black → merge into one black.",
  },
  download_svg: {
    t: "Download artwork (SVG)",
    what: "Saves the cleaned-up design as a vector artwork file.",
    why: "Open it in Wilcom, Illustrator or Inkscape to edit or digitise manually.",
  },

  // ---------------------------------------------------------------- step 3: stitch settings
  fill_spacing_mm: {
    t: "Fill thickness",
    what: "The gap between rows of stitches in filled areas. A smaller gap means thicker, fuller coverage.",
    why: "Too thin and the fabric shows through; too thick and the embroidery gets stiff and can pucker.",
    eg: "0.40 mm is standard. Use 0.35 for towels or fleece, 0.45 for thin T-shirts.",
  },
  fill_stitch_length_mm: {
    t: "Fill stitch length",
    what: "How long each stitch is inside filled areas.",
    why: "Longer stitches look smoother and use fewer stitches, but very long ones can snag.",
    eg: "3.5 mm suits most designs; 2.5 mm for small or detailed areas.",
  },
  fill_angle: {
    t: "Fill direction",
    what: "The main direction of stitch rows in filled areas, in degrees.",
    why: "Diagonal rows hide small irregularities and look good on most fabrics.",
    eg: "45 = diagonal (standard), 0 = horizontal, 90 = vertical.",
  },
  satin_spacing_mm: {
    t: "Satin thickness",
    what: "The gap between the zig-zag stitches of satin (the smooth, shiny stitch used for borders and lettering).",
    why: "Smaller gap = fuller and shinier, but more stitches.",
    eg: "0.40 mm is standard; 0.35 mm for bolder lettering.",
  },
  satin_max_width_mm: {
    t: "Widest satin shape",
    what: "Shapes narrower than this are sewn in smooth satin; wider shapes switch to fill.",
    why: "Very long satin stitches look loose and can catch on things.",
    eg: "7 mm is safe for clothing; lower it to 5 mm for caps or bags that get rubbed.",
  },
  pull_compensation_mm: {
    t: "Shrink compensation",
    what: "Makes shapes very slightly wider to make up for the fabric pulling stitches inwards.",
    why: "Without it, shapes come out thinner and small gaps can appear between colours.",
    eg: "0.2 mm for normal fabric, 0.3–0.4 mm for stretchy polo or knit shirts.",
  },
  running_stitch_length_mm: {
    t: "Outline stitch length",
    what: "Length of each stitch in thin outline lines.",
    why: "Shorter stitches follow curves more smoothly.",
    eg: "2.5 mm for most lines, 1.5–2 mm for small, tight curves.",
  },
  underlay_spacing_mm: {
    t: "Foundation spacing",
    what: "Gap between the rows of the hidden foundation stitches sewn under filled areas.",
    why: "A closer foundation supports the top stitches better on soft fabrics, but adds stitches.",
    eg: "2.5 mm standard; 2.0 mm for towels and fleece.",
  },
  trim_threshold_mm: {
    t: "Cut thread for moves longer than",
    what: "When the needle moves farther than this between areas, the machine cuts the thread.",
    why: "Prevents loose threads across your design. A lower value means fewer loose threads but more cuts (each cut takes a few seconds).",
    eg: "2 mm keeps the design clean with no hand-trimming.",
  },
  min_object_area_mm2: {
    t: "Skip shapes smaller than",
    what: "Shapes smaller than this area are not sewn.",
    why: "Tiny shapes become blobs of thread and need extra cuts.",
    eg: "0.8 mm² is about a 1 mm dot.",
  },
  underlay: {
    t: "Foundation stitches",
    what: "Hidden stitches sewn first, under the visible stitches.",
    why: "They hold the fabric steady so the top stitches look full, sharp and don't sink in.",
    eg: "Keep on. Turn off only for very small designs on firm fabric.",
  },
  vary_fill_angle: {
    t: "Change direction per colour",
    what: "Each colour's fill rows run in a slightly different direction.",
    why: "Neighbouring colours catch the light differently, so the design looks crisper.",
  },
  tie_stitches: {
    t: "Lock stitches",
    what: "Tiny back-and-forth stitches at the start and end of every section of thread.",
    why: "Like a knot – they stop the stitching from coming undone after the thread is cut.",
    eg: "Keep on for anything that will be worn or washed.",
  },
  triple_run: {
    t: "Bold outlines",
    what: "Thin outline lines are sewn three times over the same path.",
    why: "Makes thin lines thicker and more visible – like going over a pencil line with a marker.",
  },
  optimize: {
    t: "Smart stitch reduction",
    what: "Removes stitches that pile up on the same spot and hidden foundation stitches that only duplicate others.",
    why: "Fewer thread breaks, softer embroidery and faster sewing – the shape and coverage of your design stay the same.",
    eg: "Every result is checked for gaps; if any would appear, the reduction is skipped automatically. Use Optimised / Original to compare.",
  },

  // ---------------------------------------------------------------- preview & results
  view: {
    t: "Preview style",
    what: "Realistic: how the finished embroidery will look. Sewing direction: arrows show which way the needle moves. Stitch types: each kind of stitch in its own colour.",
    why: "Use Stitch types to spot loose jumps (red dashes) and thread cuts (red dots).",
  },
  variant: {
    t: "Compare versions",
    what: "Switch between the optimised stitches and the original, unreduced stitches.",
    why: "Check for yourself that the design looks the same with fewer stitches.",
  },
  stats_stitches: { t: "Stitches", what: "Total number of needle stitches.", why: "More stitches = longer sewing and stiffer embroidery." },
  stats_size: { t: "Size", what: "Final width × height of the stitched design in millimetres." },
  stats_colours: { t: "Colours", what: "Number of different threads used." },
  stats_trims: { t: "Thread cuts", what: "How often the machine cuts the thread to move to another area.", why: "Each cut adds a few seconds of sewing time." },
  stats_changes: { t: "Colour changes", what: "How often the machine switches to another thread." },
  stats_time: { t: "Estimated sewing time", what: "A rough estimate including thread cuts and colour changes.", why: "Actual time depends on your machine speed." },
  stats_thread: { t: "Thread used", what: "Approximate length of top thread needed." },
  sequence: {
    t: "Thread order",
    what: "The order in which the machine sews the colours.",
    why: "Load your machine's needles in this order. DST files do not store colours – the worksheet lists them too.",
  },
  export: {
    t: "Download",
    what: "Save the design for your embroidery machine or software.",
    why: "DST (★) works with WONYO and almost all commercial machines and opens in Wilcom.",
    eg: "Not sure? Download “Everything (.zip)”.",
  },
};

/* ---------------------------------------------------------------- tooltip engine */
const Tip = (() => {
  const el = () => document.getElementById("tooltip");
  let current = null;

  function icon(key) {
    const h = HELP[key];
    if (!h) return "";
    return `<button type="button" class="help" data-help-key="${key}" aria-label="Help: ${h.t}" aria-describedby="tooltip">?</button>`;
  }

  function content(h) {
    const e = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
    return `<b>${e(h.t)}</b><p>${e(h.what)}</p>` +
      (h.why ? `<p><span class="tip-k">Why it helps:</span> ${e(h.why)}</p>` : "") +
      (h.eg ? `<p><span class="tip-k">Example:</span> ${e(h.eg)}</p>` : "");
  }

  function show(btn) {
    const h = HELP[btn.dataset.helpKey];
    if (!h) return;
    const t = el();
    t.innerHTML = content(h);
    t.hidden = false;
    current = btn;
    const r = btn.getBoundingClientRect();
    const tw = t.offsetWidth, th = t.offsetHeight, m = 8;
    let left = Math.min(Math.max(m, r.left + r.width / 2 - tw / 2), window.innerWidth - tw - m);
    let top = r.top - th - 8;
    if (top < m) top = r.bottom + 8;
    t.style.left = `${left}px`;
    t.style.top = `${top}px`;
  }

  function hide() {
    el().hidden = true;
    current = null;
  }

  function init() {
    // replace <i data-help="key"> placeholders in static markup
    document.querySelectorAll("i[data-help]").forEach((i) => {
      const wrap = document.createElement("span");
      wrap.innerHTML = icon(i.dataset.help);
      if (i.id) wrap.firstChild && (wrap.firstChild.id = i.id);
      if (i.classList.contains("hidden")) wrap.firstChild && wrap.firstChild.classList.add("hidden");
      i.replaceWith(...wrap.childNodes);
    });
    document.addEventListener("mouseover", (ev) => { const b = ev.target.closest(".help"); if (b) show(b); });
    document.addEventListener("mouseout", (ev) => { if (ev.target.closest(".help") && current && !current.matches(":focus")) hide(); });
    document.addEventListener("focusin", (ev) => { const b = ev.target.closest(".help"); if (b) show(b); });
    document.addEventListener("focusout", (ev) => { if (ev.target.closest(".help")) hide(); });
    document.addEventListener("click", (ev) => {
      const b = ev.target.closest(".help");
      if (b) {
        ev.preventDefault(); // don't toggle the checkbox/label it sits in
        ev.stopPropagation();
        current === b && !el().hidden ? hide() : show(b);
      } else if (current) hide();
    }, true);
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") hide(); });
    window.addEventListener("scroll", hide, true);
  }

  return { icon, init, hide };
})();
