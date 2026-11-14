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
    what: "Controls how smooth the shapes are: lines and gaps thinner than this are smoothed away.",
    why: "This is the setting that decides whether thin lettering stays intact. Raise it only to simplify a busy picture.",
    eg: "0.3 mm keeps lettering intact (recommended). 0.7 mm or more simplifies – thin letters and small gaps may disappear.",
  },
  min_area_mm2: {
    t: "Remove specks",
    what: "Removes ragged or faint spots smaller than this area (dust, scan or JPG noise).",
    why: "Noise would otherwise become extra shapes with their own stitches and thread cuts. Solid small marks – dots, hyphens, full stops, holes in letters – are recognised and kept.",
    eg: "1 mm² is about the size of a 1 × 1 mm spot.",
  },
  denoise: {
    t: "Clean up picture grain",
    what: "Evens out grainy or blotchy colours (photo noise, JPG blotches) before the colours are detected.",
    why: "It does not change how smooth the shapes or letters are – use “Smallest detail to keep” for that.",
    eg: "Left (0) for clean digital logos, middle for normal pictures, right (3) for photos or scans.",
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
    t: "Smart assistant",
    what: "Helps you choose good settings and clean up pictures. Works without any AI service using built-in smart analysis; with an AI service (AI settings, top right) the advice gets better.",
    why: "Photos, blurry or low-resolution logos benefit most. Clean logos and artwork files rarely need it.",
    eg: "A phone photo of a printed T-shirt logo.",
  },
  aiSuggest: {
    t: "Suggest best settings",
    what: "Looks at your picture and recommends the number of thread colours, background handling, detail level and noise clean-up. You see the suggestions first and decide whether to apply them.",
    why: "Measures things that are hard to judge by eye, like the thinnest line in millimetres at your chosen size.",
    eg: "Warns when lines are too thin to sew and suggests a larger design width.",
  },
  aiClean: {
    t: "Picture clean-up (AI)",
    what: "An AI service redraws your picture before it is turned into stitches. Redraw = clean flat artwork; Remove background = keep the design, drop everything around it; Sharpen & enhance = fix blur and low resolution.",
    why: "Removes blur, shadows and photo backgrounds. AI may change small details or text – always compare with your original (you can switch back anytime).",
    eg: "Turns a blurry scanned logo into crisp shapes.",
  },
  aiReview: {
    t: "Check my stitches",
    what: "Checks the generated stitches for problems such as stitching that is too dense, thin shapes that may look broken, too many thread cuts or missing foundation stitches – and suggests setting changes you can apply with one click.",
    why: "Catches problems before you sew on real fabric. Works without AI; with an AI service it also looks at the preview picture like a digitizer would.",
  },
  ai_keys: {
    t: "Your API keys",
    what: "AI services need a key from your own account with them. The key stays in this browser and is sent only with AI requests - the server uses it for that request and never saves it.",
    why: "You stay in control of costs and can remove the key anytime.",
    eg: "Google Gemini has a free tier - a good place to start.",
  },
  ai_vision_use: {
    t: "Advice & stitch checks",
    what: "Which service answers “Suggest best settings” and “Check my stitches”. “Built-in” uses the app's own analysis and needs no key.",
  },
  ai_edit_use: {
    t: "Picture clean-up",
    what: "Which service redraws or cleans your pictures. Only services that can create pictures are listed (Claude and Ollama cannot).",
  },
  ai_base_url: {
    t: "Address",
    what: "Where the service runs. Only needed for Ollama on another computer or for OpenAI-compatible services.",
    eg: "http://localhost:11434 for Ollama, http://localhost:1234/v1 for LM Studio.",
  },
  ai_model: {
    t: "Model",
    what: "Which of the service's AI models to use. “Automatic” picks a suitable current model for you.",
    why: "Press “Connect & load models” to see what your account offers. Bigger models give better results but cost more.",
  },
  ai_remember: {
    t: "Remember on this device",
    what: "Keeps your settings and keys after you close the browser. Leave it off on shared computers - then they are forgotten when you close the browser.",
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
    why: "Fill: textured rows for large areas. Satin: smooth, shiny zig-zag for narrow shapes like lettering and borders. Outline: a single thin line. Decorative outlines and borders (backstitch, stemstitch, zigzag, E-stitch…) sew only the edge, in a decorative way. Border width and gap are in Advanced stitch settings.",
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
    t: "Undo / Redo",
    what: "Undo takes back your changes to the design one by one (shape edits, colours, stitch styles, merging, “Update preview”) - up to 50 steps. Redo brings back what you undid.",
    why: "Try things freely: nothing is lost until you make a new change after undoing.",
    eg: "Undo: Ctrl+Z (Cmd+Z). Redo: Ctrl+Y or Ctrl+Shift+Z.",
  },
  shape_style: {
    t: "Stitch style for the selected shapes",
    what: "How the selected shapes are sewn. “Same as colour” follows the colour list below; “Don't stitch” leaves the shape out so the fabric shows.",
    why: "Fill suits large areas, Satin gives a smooth shine on narrow shapes, Outline sews just the edge. The advanced fills (offset, spiral, contour, radial, shaded, stipple, maze, cross stitch) and decorative fills (motif, pattern, jagged) fill the shape with another texture; raised satin pads a narrow shape; appliqué sews a fabric piece on (the machine stops so you can lay it). The decorative outlines (double run, backstitch, stemstitch, sculpture run) and borders (zigzag, E-stitch) are sewn along a line, or around the edge of a filled shape instead of filling it.",
    eg: "Remove a stray speck with “Don't stitch”. Use an E-stitch border around an appliqué piece, or stemstitch for a hand-embroidered look.",
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
  text_mode: {
    t: "Text & fine details",
    what: "How carefully small lettering and fine lines are kept. Automatic measures your picture: when it finds text, letter shapes, holes and punctuation are kept sharp.",
    why: "Small text needs every detail; photos and rough sketches look cleaner when tiny details are smoothed away.",
    eg: "Keep sharp: names, slogans, Arabic script, small print. Simplify: photos, textured artwork.",
  },
  brightness: {
    t: "Brightness",
    what: "Makes the picture lighter or darker before thread colours are picked.",
    why: "Helps when a dark photo or scan makes colours look muddy or similar.",
    eg: "Usually 0. Try +20 for a dark phone photo.",
  },
  contrast: {
    t: "Contrast",
    what: "Makes the difference between light and dark parts stronger or weaker.",
    why: "More contrast separates text and shapes from a faded or greyish background.",
    eg: "+20 to +40 for faded scans or photos of printed logos.",
  },
  sharpen: {
    t: "Sharpen edges",
    what: "Crisps up soft, blurry edges before the shapes are traced.",
    why: "Blurry pictures make letters blobby; sharpening keeps their corners and gaps.",
    eg: "Low or Medium for small or blurry pictures. Leave Off for clean artwork.",
  },
  border_width_mm: {
    t: "Border width",
    what: "How wide zigzag and E-stitch borders are sewn. A stroked line from vector artwork keeps its own width when it is 1 mm or wider.",
    why: "A wider border covers a raw edge (for example around appliqué) better; a narrow one looks finer.",
    eg: "2 mm for most borders; 3–4 mm to cover the edge of an appliqué piece.",
  },
  border_spacing_mm: {
    t: "Border gap",
    what: "The gap between the stitches of zigzag and E-stitch borders.",
    why: "These are open borders: a larger gap looks lighter and lets the fabric show; a small gap gets close to a satin border.",
    eg: "1.2 mm for an open look; 0.6–0.8 mm for a denser border.",
  },
  optimize_order: {
    t: "Smart sewing order",
    what: "Within each colour, sews the shapes in the order that keeps the jumps between them shortest (nearest-neighbour, then improved by swapping stretches of the order).",
    why: "Shorter jumps mean fewer thread cuts and less machine time; the stitches themselves do not change.",
    eg: "Leave it on. Turn it off only to compare with the plain nearest-shape-next order.",
  },
  fill_pattern: {
    t: "Fill texture",
    what: "How the needle points of tatami fill rows shift from row to row. Standard spreads them in a brick pattern; diagonal lines them up into visible twill lines; random hides every line.",
    why: "The pattern of needle points is visible on the finished fill: it can be hidden, or used as a texture.",
    eg: "Standard for most designs; Diagonal for a woven look; Random for a smooth, even surface.",
  },
  stipple_spacing_mm: {
    t: "Stipple / maze line gap",
    what: "The distance between neighbouring lines of the stipple and maze fills.",
    why: "A larger gap makes a lighter, quilted look; a smaller gap covers more of the fabric.",
    eg: "2 mm for a light background texture; 1–1.5 mm to cover more.",
  },
  cross_cell_mm: {
    t: "Cross stitch size",
    what: "The size of each cross in the cross stitch fill.",
    why: "Larger crosses look like coarse hand cross stitch; smaller ones show more detail.",
    eg: "3 mm (the usual size); 2 mm for small designs.",
  },
  motif: {
    t: "Motif",
    what: "The small figure that motif outlines and motif fills repeat: wave, scallop, loop, leaf, diamond or saw tooth.",
    why: "Motifs turn a plain outline or fill into a decorative border or texture.",
    eg: "Wave or scallop for soft borders; leaf for a vine; saw tooth for a bold edge.",
  },
  motif_size_mm: {
    t: "Motif size",
    what: "How long each motif is along the line (and how far apart the rows of a motif fill are).",
    why: "Small motifs look fine and dense; large ones read from a distance.",
    eg: "5–8 mm for most designs; 3–4 mm only with simple motifs such as the wave.",
  },
  pattern: {
    t: "Pattern fill pattern",
    what: "The pattern pressed into a pattern fill: the needle points of the fill rows are placed on its lines, so it shows as an embossed texture.",
    why: "Adds texture to a large area in one colour, like woven or quilted fabric.",
    eg: "Diamonds or scales for badges and backgrounds; bricks or squares for a tiled look.",
  },
  pattern_size_mm: {
    t: "Pattern fill tile size",
    what: "The size of one tile of the pattern fill (one diamond, square, brick or scale).",
    why: "The pattern must be large enough to see: at least a few rows of stitches per tile.",
    eg: "6–10 mm for most designs.",
  },
  jagged_mm: {
    t: "Jagged fill roughness",
    what: "How far the row ends of a jagged fill wander in and out of the shape's edge.",
    why: "Rough edges look like fur, grass or feathers instead of a cut-out shape.",
    eg: "1–2 mm for fur; 3 mm or more for grass.",
  },
  knockdown: {
    t: "Knockdown",
    what: "An open, two-layer fill sewn first under small text, satin and outlines, reaching 2 mm beyond them. It is sewn as its own colour: load thread in the fabric's colour.",
    why: "On towels, fleece and other pile fabric the loops swallow small details; the knockdown flattens the pile so the details stay on top.",
    eg: "Turned on by the Towel and Fleece fabric presets. Leave it off on flat fabric.",
  },
  photo_stitch: {
    t: "Photo stitch",
    what: "Sews the whole picture in one dark thread as rows: each row zigzags as wide as the picture is dark at that spot, so dark areas fill in and light areas stay open. The shading comes from the colours of the shapes, so extract a photo with several shades (for example 8 colours) first.",
    why: "Photos have soft shading that separate colour areas cannot show; rows of varying width reproduce it with one thread.",
    eg: "Portraits and pets in one colour on a light fabric. Remove the background in step 2 so only the subject is sewn.",
  },
  photo_style: {
    t: "Photo stitch style",
    what: "Rows: one dark thread in rows that zigzag wider where the picture is darker. Reef: one dark thread as a meander that never crosses itself, tight where dark and open where light. Rows in every colour: each thread colour sews rows as wide as that colour is present, and neighbouring colours share the rows where they meet.",
    why: "Rows and Reef shade a photo with a single thread; every colour keeps the colours of a colour photo and blends them softly.",
    eg: "Rows for portraits; Reef for a light, artistic look; every colour for colourful photos extracted with 6–10 colours.",
  },
  photo_row_mm: {
    t: "Photo stitch row gap",
    what: "The distance between the rows of the photo stitch.",
    why: "Closer rows show finer detail but take more stitches; wider rows give a bolder, graphic look.",
    eg: "1 mm for most photos; 0.8 mm for small, detailed ones; 1.5 mm for a bold look.",
  },
  fill_underlay: {
    t: "Foundation under fills",
    what: "Stitches sewn first, under each filled area, to hold the fabric still. Edge run + lattice is a run just inside the outline plus an open grid; the double lattice adds a second, crossing grid.",
    why: "Without a foundation the fill pulls the fabric in and the edges look ragged; stretchy or fluffy fabric needs more of it.",
    eg: "Standard for most fabric; double lattice for fleece and towels; edge run only for small or light fills.",
  },
  satin_underlay: {
    t: "Foundation under satin",
    what: "Stitches sewn first, under each satin column. An edge run sits just inside both edges; a zigzag runs across the column and lifts the satin. Columns narrower than 2.5 mm get a centre run instead of a zigzag.",
    why: "It keeps satin edges crisp and stops the stitches sinking into soft fabric.",
    eg: "Edge run for most fabric; edge run + zigzag for T-shirts and polos; double zigzag for fleece or a raised look.",
  },
  push_compensation_mm: {
    t: "Push compensation",
    what: "Shortens the ends of fills and satin columns by this much. Stitches push the fabric outwards along the column, so shapes come out a little longer than drawn.",
    why: "Keeps lettering and columns their true length, so they do not run into each other.",
    eg: "0 (off) for most designs; 0.1–0.3 mm on soft or stretchy fabric if shapes come out too long. Check with a test sew-out.",
  },
  shape_guide: {
    t: "Guide line",
    what: "A line you draw across the shape. With “Guided satin” the satin stitches cross it at right angles all along it, from edge to edge; with “Florentine fill” every fill row is a copy of it, so the whole fill bends with it; with “Carved fill” the fill's needle points are placed along it, so the line shows pressed into the fill.",
    why: "You decide exactly how a curved satin turns or how a fill flows, where the automatic direction is not what you want.",
    eg: "Choose the stitch style first, then press “Draw guide line” and drag along the middle of a curved band (satin) or in the direction the rows should flow (fill).",
  },
  shape_blend: {
    t: "Blend into colour",
    what: "With “Blend fill”, the shape is sewn in its own colour with rows that open up across it, and again in the colour chosen here, shaded the other way. Together they always cover the shape fully, so one colour fades into the other along the stitch direction.",
    why: "A soft change from one thread colour to another - sunsets, skies, shading on petals - that separate colour areas cannot show.",
    eg: "Set the direction across which the colours should change (for example vertical for a horizon). Choose “Fabric” to let the colour fade out into the fabric instead.",
  },
  shape_underlay: {
    t: "Foundation",
    what: "The foundation stitches sewn under the selected shapes before their top stitching. The choices depend on how the shapes are sewn: fills and straight satin columns have their own lists; satin that follows a curve keeps its own foundation.",
    why: "A heavier foundation helps a shape that sits on soft fabric or must look raised; a lighter one keeps small shapes flat.",
    eg: "Leave “Same as design” unless one shape needs more or less support than the rest.",
  },
  fabric: {
    t: "Fabric",
    what: "The fabric you will embroider. Choosing one fills in the shrink compensation, thickness and foundation settings that suit it, and adds the backing and needle to use to the production worksheet.",
    why: "Stretchy and fluffy fabrics pull stitches in and swallow them more than firm cotton, so they need more compensation and a stronger foundation.",
    eg: "General keeps the standard settings. Pick T-shirt for jersey, Fleece for sweatshirts. Every value can still be changed afterwards; sew a test piece on new fabrics.",
  },
  thread_weight: {
    t: "Thread",
    what: "The thread you will load on the machine. Small lettering is sewn as a smooth satin that follows each stroke of a letter; thin strokes are widened to the narrowest column the thread can sew, only as far as the gaps between letters allow.",
    why: "Fine 60 wt thread lies thinner, so letter strokes can stay narrower (0.6 mm instead of 1.0 mm) and letters stay open and readable. Use it with a 65/9 or 70/10 needle.",
    eg: "Standard 40 wt for normal designs. Fine 60 wt for lettering under about 5 mm tall or fine script.",
  },
  stitch_lettering: {
    t: "Lettering style",
    what: "Sews a shape as letter strokes: a smooth satin column that follows each stroke, widened only as much as the gaps between letters allow.",
    why: "Automatic already uses it for thin text; choose it for bolder text or script you want sewn like lettering.",
  },
  satin_max_width_mm: {
    t: "Widest satin shape",
    what: "Shapes narrower than this are sewn in smooth satin; wider shapes switch to fill. Satin stitches longer than 7 mm are split into shorter ones, with the split points staggered from row to row so no seam line shows.",
    why: "Long satin stitches look loose and can catch on things; splitting keeps the satin shine on wider shapes, but very wide satin still snags more easily than fill.",
    eg: "10 mm for most designs; 7 mm or less for caps, bags and workwear that get rubbed.",
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
    why: "Keep it low so dots and punctuation in lettering are stitched – tiny dots are enlarged to the smallest dot the machine can sew, where there is room.",
    eg: "0.05 mm² keeps even a 0.25 mm dot. Raise it (e.g. 1) only to drop small stray shapes.",
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
  smart_direction: {
    t: "Smart direction",
    what: "Looks at every section of the design on its own and picks the stitch direction that suits its shape. Letters, curved text, rings and other narrow shapes get satin that follows their curve; narrow parts growing out of a large shape are sewn separately; each filled area gets its own best angle.",
    why: "One direction for a whole colour suits some shapes and spoils others. Off = one direction per colour, as before. Directions and stitch styles you set yourself for a shape or a colour always win, and “same direction for the whole design” still works.",
    eg: "Turn on for logos with lettering, curved or circular text. Sew a test piece first: more of the design becomes satin.",
  },
  tie_stitches: {
    t: "Lock stitches",
    what: "Small back-and-forth stitches wherever the thread is cut (start and end of each section). They follow the stitching path and are never shorter than the thread can take.",
    why: "Like a knot – they stop the stitching from coming undone after the thread is cut. Short hops without a cut need no lock, which keeps tiny lettering clean.",
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
  pin: {
    t: "Pin preview",
    what: "Keeps the preview in view at the top while you scroll through the settings below it.",
    why: "You see every change on the design straight away. Click again for the normal layout, where the preview scrolls with the page.",
  },
  reference: {
    t: "Reference",
    what: "Shows the original picture (or the design as first extracted) faded under your shapes, exactly lined up with them - also when you zoom or move.",
    why: "Compare your edits with the artwork you started from. Use the slider to make it fainter or stronger.",
  },
  layers_drag: {
    t: "Drag to merge colours",
    what: "Drag a colour by its ⠿ handle (or drag a colour chip) and drop it on another colour. Every area of the dragged colour takes the colour you drop it on, and both become one region.",
    why: "Two shades of the same thread (for example light blue and blue, or two navies) sew as separate passes with overlaps and an extra thread change. Merged, they sew as one clean area. Undo separates them again.",
  },
  colour_select: {
    t: "Select by colour",
    what: "Selects every area sewn in that thread colour, all over the design.",
    why: "Change the stitch style, direction, thickness or thread colour of all those areas at once. Shift-click to add another colour. Drag a chip onto another chip to merge the two colours.",
  },
  shape_colour: {
    t: "Thread colour",
    what: "With all areas of a colour selected, picking a colour changes the thread for every one of them. With only some areas selected, they move to another colour (existing or new).",
    why: "Fix a wrong colour everywhere at once, or give a single part of the design its own thread.",
  },
  shape_edit: {
    t: "Edit shape",
    what: "Change the outline of the selected areas by hand: paint area on, erase it, or draw an outline to add or cut away.",
    why: "Fix a broken letter, close a gap, remove a stray bit or tidy an edge the automatic tracing got wrong. Every change can be undone.",
  },
  shape_edit_tools: {
    t: "Editing tools",
    what: "Paint / Erase use the brush size. Lasso +/- add or cut the area inside the outline you draw. Grow / Shrink change the size by 0.2 mm all round; Smooth rounds off jagged edges.",
    why: "Area you add is taken away from other colours underneath, so nothing is stitched twice. Zoom with the mouse wheel or two fingers; hold Space (or use Move) to pan.",
  },
  view: {
    t: "Preview style",
    what: "Realistic: how the finished embroidery will look. Sewing direction: arrows show which way the needle moves. Stitch types: each kind of stitch in its own colour. Machine check: circles mark spots where the thread may break or a detail may not show.",
    why: "Use Stitch types to spot loose jumps (red dashes) and thread cuts (red dots). Use Machine check before sending small lettering to the machine – orange circles are strokes too thin to sew cleanly: make the design bigger or choose fine 60 wt thread.",
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
    eg: "Tick the files you need, then press Download. One file downloads as it is; several come together in one .zip. Not sure? Keep DST and add the worksheet.",
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
