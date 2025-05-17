{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 15,
      "name": "$15",
      "type": "VARCHAR"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "IN",
        "operator": "IN",
        "operands": [
          {
            "kind": "INPUT_REF",
            "index": 4,
            "name": "$4",
            "type": "VARCHAR"
          }
        ],
        "query": [
          {
            "relOp": "LogicalProject",
            "project": [
              {
                "kind": "INPUT_REF",
                "index": 2,
                "name": "$2",
                "type": "VARCHAR"
              }
            ],
            "id": "2",
            "inputs": [
              {
                "relOp": "LogicalFilter",
                "condition": {
                  "kind": "AND",
                  "operator": "AND",
                  "type": "BOOLEAN",
                  "operands": [
                    {
                      "kind": "EQUALS",
                      "operator": "=",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "INPUT_REF",
                          "index": 1,
                          "name": "$1",
                          "type": "VARCHAR"
                        },
                        {
                          "kind": "LITERAL",
                          "value": "Korean",
                          "type": "VARCHAR",
                          "nullable": false,
                          "precision": -1
                        }
                      ]
                    },
                    {
                      "kind": "NOT",
                      "operator": "NOT",
                      "type": "BOOLEAN",
                      "operands": [
                        {
                          "kind": "LIKE",
                          "operator": "LIKE",
                          "type": "BOOLEAN",
                          "operands": [
                            {
                              "kind": "INPUT_REF",
                              "index": 1,
                              "name": "$1",
                              "type": "VARCHAR"
                            },
                            {
                              "kind": "LITERAL",
                              "value": "%Japanese%",
                              "type": "CHAR",
                              "nullable": false,
                              "precision": 10
                            }
                          ]
                        }
                      ]
                    }
                  ]
                },
                "variableset": "[]",
                "id": "1",
                "inputs": [
                  {
                    "relOp": "LogicalTableScan",
                    "table": "set_translations",
                    "id": "0",
                    "inputs": []
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "sets",
          "id": "3",
          "inputs": []
        }
      ]
    }
  ]
}