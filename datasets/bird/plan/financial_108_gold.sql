{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "INTEGER"
    },
    {
      "column": 2,
      "type": "DATE"
    }
  ],
  "dir": [
    "DESCENDING",
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 17,
          "name": "$17",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 10,
          "name": "$10",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 18,
          "name": "$18",
          "type": "DATE"
        }
      ],
      "id": "3",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 0,
                "name": "$0",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 19,
                "name": "$19",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "district",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "client",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}