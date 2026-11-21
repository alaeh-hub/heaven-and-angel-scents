/** Sized copies of a scent render: `-s` is 360px tall, `-m` 760px; full size is the original. */
export const thumb = (src, size) => src.replace(/\.webp$/, `-${size}.webp`);
