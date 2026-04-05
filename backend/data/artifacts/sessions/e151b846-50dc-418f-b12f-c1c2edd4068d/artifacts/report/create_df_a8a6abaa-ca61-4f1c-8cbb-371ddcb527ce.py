df = pd.read_parquet('data.parquet')
for i, (key, group_df) in enumerate(df.groupby('RAD'), 1):
    group_df.to_parquet(f'result_{i}.parquet', index=False)
    print(f"Group {key}: {group_df.shape}")