{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "DESCENDING"
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
          "index": 118,
          "name": "$118",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 10,
          "name": "$10",
          "type": "INTEGER"
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
                "index": 8,
                "name": "$8",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 116,
                "name": "$116",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "Match",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "Team",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}