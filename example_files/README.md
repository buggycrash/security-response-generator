# example_files/

Reference/starter material per jurisdiction, to make it faster to populate a
new engagement's `customer_standards/` folder. Each subfolder is a
jurisdiction you might be working with:

```
example_files/
├── example_private_context/
├── Federal/
├── VA/
├── PA/
├── CA/
├── MD/
└── HI/
```

This folder is committed to the repo. Unlike the customer folders under
`engagements/`, nothing here is tied to one specific engagement, and all
information is fully public.

## How to use this when starting a new engagement

1. Create the engagement, for example `srg create-engagement virginia`.
2. Find the subfolder matching your customer's jurisdiction.
3. Read the subfolder's `README.md` for customer context.
4. Copy (not move) relevant files into the `customer_standards/` path printed
   by `create-engagement`.
5. Adjust/trim as needed for the specific engagement — its standards folder
   should reflect what's actually true for *this* customer, not just a
   generic copy of the jurisdiction's example material.
6. Run `srg ingest` from any directory.

Each jurisdiction subfolder has its own `README.md` stub (Folder Name /
Included Files / Useful Links) to be filled in as that jurisdiction's
material is researched and added.
