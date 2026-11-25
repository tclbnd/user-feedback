# ktxkit

Check for the KTX textures (`.ktx`) exported by the technical art pipeline.
`ktxinfo.py` prints one JSON summary per file, so a texture can be checked
before it goes into the engine build.

## Input

KTX 1.1 files as the exporters on the different build machines write them:
plain 1D, 2D and 3D textures, texture arrays and cube maps, with or without
mipmaps and key/value data. Uncompressed textures use `GL_UNSIGNED_BYTE` with
`GL_RED`, `GL_RG`, `GL_RGB`, `GL_BGR`, `GL_RGBA`, `GL_BGRA`, `GL_LUMINANCE`,
`GL_LUMINANCE_ALPHA` or `GL_ALPHA`, or one of the packed 16-bit types
`GL_UNSIGNED_SHORT_5_6_5`, `_4_4_4_4`, `_5_5_5_1` and their `_REV` forms.
Compressed formats are listed but not decoded.

## Output

| field | meaning |
|---|---|
| `endianness` | `"little"` or `"big"`, the byte order the file was written in |
| `gl_type`, `gl_format`, `gl_internal_format` | from the header (integers) |
| `width`, `height`, `depth`, `array_elements`, `faces` | from the header (integers, as stored) |
| `mip_levels` | number of levels present in the file |
| `key_values` | object of key → value text; a value ending in one NUL is shown without it |
| `levels` | one `[width, height, imageSize]` per level present, imageSize as stored in the file |
| `complete` | `true` when the data of every level is in the file |
| `first_texels` | for the first level, the first texel of each image in file order (array elements, faces, depth slices), at most 8 |
| `last_texel` | for the first level, the bottom-right texel of the last image |
| `smallest_texel` | the first texel of the first image of the last level present |

Texels are shown as the RGBA value OpenGL produces when it reads the texel,
`"#rrggbbaa"` lower case; packed components are scaled to 8 bits as
round(c × 255 / max). A texel is `null` for compressed formats or when it is
not in the file.

## Layout

- `ktxkit/texture.py` – header, key/value data, levels, texels
- `ktxinfo.py` – command line

## Running

    python3 ktxinfo.py examples/crate.ktx examples/sky.ktx
    python3 -m unittest discover -s tests
