# Port Image Builder

Builds a flashable recovery zip for RMX3371 ROM ports.

## Usage

1. Add the 13 ROM images to `images/`. See `images/required_images.txt` for the names.
2. Fill in `rom.conf`:

   ```
   ROM_NAME=PORT_NAME
   DEVICE_NAME=RMX3371
   DEV_NAME=YOUR_NAME
   ```

3. Build the zip:

   ```bash
   ./build_zip.sh
   ```

The zip is named from `rom.conf`, for example `COS17_RMX3371_BY_Amrutesh.zip`.

