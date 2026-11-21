// "About the brand" copy in one place.
// TODO(content): this is DRAFT copy; replace it with the real story before
// sharing the link. Keep `lede` ~2 sentences, point bodies ~25 words.
// `photos`: `main` is landscape 4:3, `detail` portrait 4:5; WebP/JPG ~1600px.

export const ABOUT = {
  title: 'About Heaven & Angel Scents.',
  photos: {
    // Unsplash stock photos (Unsplash License); swap files in static/img/about/.
    //   bottles.webp Lera Ginzburg, https://unsplash.com/photos/N8WxMVijPKw
    //   dropper.webp Fulvio Ciccolo, https://unsplash.com/photos/dtLGvOl0hEs
    main: { src: '/static/img/about/bottles.webp', alt: 'Five perfume bottles with blank labels standing in raking light and shadow' },
    detail: { src: '/static/img/about/dropper.webp', alt: 'A perfumer holding a glass dropper over a bottle, about to add golden liquid' },
  },
  lede:
    'We craft eau de parfum for those who want a scent that feels unmistakably their own, and we supply the distributors and resellers who place it in their hands.',
  points: [
    {
      key: 'people',
      title: "Who's behind it",
      body: 'A small in-house team blends, bottles and cares for our partners together, so whoever answers your message knows the very scent you are asking about.',
    },
    {
      key: 'years',
      title: 'How long we have been at it',
      body: 'We have grown the range one scent at a time, keeping what customers return for and refining the rest, so partners begin with that experience from their first order.',
    },
    {
      key: 'formula',
      title: 'What makes our formulations different',
      body: 'Every fragrance is made in small runs and bottled at 85 ml, its top, heart and base notes balanced to open bright and settle warm on the skin.',
    },
  ],
};
