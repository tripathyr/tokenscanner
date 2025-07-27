import os

def replace_flodata_interactive():
    input_file = input("Enter the path to your SQL file: ").strip()

    if not os.path.isfile(input_file):
        print(f"❌ File '{input_file}' does not exist.")
        return

    try:
        with open(input_file, 'r', encoding='utf-8') as f:
            content = f.read()

        count = content.count("flodata")
        if count == 0:
            print("✅ No occurrences of 'flodata' found.")
        else:
            print(f"🔄 Found {count} occurrences of 'flodata' to replace.")

        updated_content = content.replace("flodata", "floData")

        # Generate output filename
        base, ext = os.path.splitext(input_file)
        output_file = f"{base}_floData{ext}"

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(updated_content)

        print(f"✅ Updated file saved as: {output_file}")

    except Exception as e:
        print(f"⚠️ Error: {e}")

# Run the function
replace_flodata_interactive()

