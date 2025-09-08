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
          "index": 12,
          "name": "$12",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 3,
          "name": "$3",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 9,
          "name": "$9",
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
                "index": 1,
                "name": "$1",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 8,
                "name": "$8",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "loan",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "trans",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}