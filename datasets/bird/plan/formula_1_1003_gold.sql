{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 4,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "6",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 4,
          "name": "$4",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 5,
          "name": "$5",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 7,
          "name": "$7",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 20,
          "name": "$20",
          "type": "VARCHAR"
        },
        {
          "kind": "OTHER_FUNCTION",
          "operator": "JULIANDAY",
          "type": "INTEGER",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 6,
              "name": "$6",
              "type": "DATE"
            }
          ]
        }
      ],
      "id": "5",
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
                "index": 10,
                "name": "$10",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 16,
                "name": "$16",
                "type": "INTEGER"
              }
            ]
          },
          "id": "4",
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
                    "index": 11,
                    "name": "$11",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "drivers",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "driverStandings",
                  "id": "1",
                  "inputs": []
                }
              ]
            },
            {
              "relOp": "LogicalTableScan",
              "table": "races",
              "id": "3",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}