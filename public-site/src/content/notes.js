// "How our scents unfold" copy (ScentJourney), like about.js.
// Notes follow the published pyramids of each bottle's inspiration.
// TODO(content): confirm against your own formulas. Keep 1-3 notes per
// layer; the first of each also floats over its bottle.

/** The two bottles in the scene, left to right (see PerfumeScene). */
export const SCENTS = [
  { key: 'uriel', name: 'Uriel H1' },
  { key: 'raphael', name: 'Raphael A3' },
];

export const LAYERS = [
  {
    name: 'Top notes',
    when: 'First 15 minutes',
    title: 'The first impression.',
    body: 'Bright sparks that greet the skin, then lift away.',
    notes: { uriel: ['Bergamot', 'Pineapple', 'Passion fruit'], raphael: ['Lemon', 'Mint', 'Bergamot'] },
  },
  {
    name: 'Heart notes',
    when: '2 to 4 hours',
    title: 'The character.',
    body: 'The soul of the scent, and the part they remember.',
    notes: { uriel: ['Freesia', 'Peach', 'Pink peony'], raphael: ['Ginger', 'Seagrass', 'Jasmine'] },
  },
  {
    name: 'Base notes',
    when: '6 hours and beyond',
    title: 'What stays.',
    body: 'Deep, warm notes that linger long after.',
    notes: { uriel: ['Amber', 'Cedar', 'Musk'], raphael: ['Sandalwood', 'Cedar', 'Musk'] },
  },
];
