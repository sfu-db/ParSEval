{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "DATE"
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
          "index": 3,
          "name": "$3",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 14,
          "name": "$14",
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
                "index": 12,
                "name": "$12",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "circuits",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "races",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}